"""Run the full pipeline: manifest -> parsed rows -> cross-year entity matching -> outputs."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .normalize import entity_key, normalize_jurisdiction
from .parse import assign_parents, parse_file

LONG_COLS = [
    "parent_ticker", "parent_cik", "parent_name", "fiscal_year", "period_end", "accession",
    "subsidiary_name", "immediate_parent", "relationship", "level", "jurisdiction_raw",
    "country", "state_or_region", "ownership_pct", "entity_id", "row_order", "parse_method",
    "flags", "source_url", "source_file",
]


def _full_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


RAW_NAME = re.compile(
    r"^(?P<ticker>[A-Za-z0-9.\-]+)_FY(?P<fy>\d{4})_(?P<acc>\d{10}-\d{2}-\d{6})_ex21\.(htm|html|txt|md)$",
    re.I,
)


def load_manifest(manifest_path: Path) -> tuple[list[dict], list[dict]]:
    """Rows from data/manifest.csv, plus any file in data/raw/ that the manifest does not list.

    A file added by hand is picked up automatically if it is named
    TICKER_FY2025_0000000000-25-000000_ex21.htm (ticker, fiscal year, accession number).
    """
    base = manifest_path.parent
    rows, skipped = [], []
    if manifest_path.exists():
        with open(manifest_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    listed = {Path(r["local_file"]).name for r in rows}
    for f in sorted((base / "raw").glob("*")):
        if f.name in listed or f.name.startswith("."):
            continue
        m = RAW_NAME.match(f.name)
        if not m:
            skipped.append({"parent_ticker": "", "fiscal_year": "", "accession": "",
                            "source_file": f"raw/{f.name}", "rows_parsed": 0,
                            "status": "SKIPPED: file name does not follow TICKER_FY2025_ACCESSION_ex21.htm"})
            continue
        t = m["ticker"].upper()
        rows.append({"ticker": t, "cik": "", "parent_name": t, "form": "10-K",
                     "fiscal_year": m["fy"], "period_end": "", "accession": m["acc"],
                     "source_url": "", "local_file": f"raw/{f.name}"})
    return rows, skipped


def load_records(manifest_path: Path) -> tuple[pd.DataFrame, list[dict]]:
    base = manifest_path.parent
    records = []
    manifest, qa = load_manifest(manifest_path)
    for m in manifest:
        src = base / m["local_file"]
        if not src.exists():
            qa.append({"parent_ticker": m["ticker"], "fiscal_year": m["fiscal_year"],
                       "accession": m["accession"], "source_file": m["local_file"],
                       "rows_parsed": 0, "status": "CHECK: file listed in manifest but missing"})
            continue
        rows = assign_parents(parse_file(src), m["parent_name"])
        for r in rows:
            country, region = normalize_jurisdiction(r["jurisdiction_raw"])
            records.append({
                "parent_ticker": m["ticker"], "parent_cik": m["cik"],
                "parent_name": m["parent_name"], "fiscal_year": int(m["fiscal_year"]),
                "period_end": m.get("period_end", ""), "accession": m["accession"],
                **r, "country": country, "state_or_region": region,
                "source_url": m["source_url"], "source_file": m["local_file"],
            })
        names = [r["subsidiary_name"] for r in rows]
        qa.append({
            "parent_ticker": m["ticker"], "fiscal_year": int(m["fiscal_year"]),
            "accession": m["accession"], "source_file": m["local_file"],
            "rows_parsed": len(rows),
            "duplicate_names": len(names) - len(set(names)),
            "missing_jurisdiction": sum(1 for r in rows if not r["jurisdiction_raw"]),
            "multi_level": int(any(r["level"] > 0 for r in rows)),
            "parse_method": ",".join(sorted({r["parse_method"] for r in rows})) or "NONE",
            "status": "OK" if rows else "CHECK: no rows parsed (image/PDF exhibit or unusual layout?)",
        })
    df = pd.DataFrame(records)
    if df.empty:
        raise SystemExit("No subsidiaries parsed. Check data/raw/ and data/manifest.csv.")
    return df, qa


def match_entities(df: pd.DataFrame) -> pd.DataFrame:
    """Give each subsidiary a stable entity_id across years within its parent group.

    1. identical name (ignoring case/punctuation)  -> same entity
    2. same name once legal-form words are stripped, and unambiguous in both years
       -> same entity, recorded as a name change
    Anything else is a new entity.
    """
    df = df.copy()
    df["entity_id"] = ""
    df["name_change_note"] = ""
    for ticker, g in df.groupby("parent_ticker"):
        known: dict[str, str] = {}          # full key -> entity id
        stripped_owner: dict[str, set] = defaultdict(set)  # stripped key -> entity ids
        counter = 0
        for fy in sorted(g["fiscal_year"].unique()):
            yr = g[g["fiscal_year"] == fy]
            stripped_this_year = yr["subsidiary_name"].map(entity_key).value_counts()
            for idx, r in yr.iterrows():
                fk, sk = _full_key(r["subsidiary_name"]), entity_key(r["subsidiary_name"])
                eid = known.get(fk)
                if eid is None and stripped_this_year[sk] == 1 and len(stripped_owner[sk]) == 1:
                    eid = next(iter(stripped_owner[sk]))
                    df.at[idx, "name_change_note"] = "name differs from earlier year"
                if eid is None:
                    counter += 1
                    eid = f"{ticker}-S{counter:04d}"
                known[fk] = eid
                stripped_owner[sk].add(eid)
                df.at[idx, "entity_id"] = eid
    return df


def timeline(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    years_by_parent = df.groupby("parent_ticker")["fiscal_year"].apply(lambda s: sorted(set(s)))
    rows, events = [], []
    for (ticker, eid), g in df.groupby(["parent_ticker", "entity_id"]):
        years = years_by_parent[ticker]
        g = g.sort_values("fiscal_year")
        latest = g.iloc[-1]
        present = set(g["fiscal_year"])
        names = list(dict.fromkeys(g["subsidiary_name"]))
        if present == set(years):
            status = "Listed every year"
        elif min(present) > years[0] and max(present) == years[-1]:
            status = f"New in FY{min(present)}"
        elif max(present) < years[-1]:
            status = f"No longer listed after FY{max(present)}"
        else:
            status = "Intermittent"
        if len(names) > 1:
            status += " · name changed"
        row = {
            "parent_ticker": ticker, "parent_name": latest["parent_name"], "entity_id": eid,
            "subsidiary_name (latest)": latest["subsidiary_name"],
            "earlier names": "; ".join(names[:-1]),
            "country": latest["country"], "state_or_region": latest["state_or_region"],
            "jurisdiction (as filed)": latest["jurisdiction_raw"],
            "first_listed": f"FY{min(present)}", "last_listed": f"FY{max(present)}",
            "status": status,
        }
        for fy in years:
            row[f"FY{fy}"] = "✓" if fy in present else ""
        rows.append(row)
        prev_name, prev_j = None, None
        for fy in years:
            hit = g[g["fiscal_year"] == fy]
            if hit.empty:
                if prev_name is not None and fy > max(present) and fy == min(y for y in years if y > max(present)):
                    events.append((ticker, fy, "Removed", prev_name, "", "Not in this year's Exhibit 21"))
                continue
            h = hit.iloc[0]
            if prev_name is None and fy != years[0]:
                events.append((ticker, fy, "Added", h["subsidiary_name"], h["jurisdiction_raw"],
                               "First appears in this year's Exhibit 21"))
            elif prev_name is not None and h["subsidiary_name"] != prev_name:
                events.append((ticker, fy, "Renamed", h["subsidiary_name"], h["jurisdiction_raw"],
                               f"Previously listed as “{prev_name}”"))
            elif prev_j is not None and h["jurisdiction_raw"] != prev_j:
                events.append((ticker, fy, "Jurisdiction changed", h["subsidiary_name"],
                               h["jurisdiction_raw"], f"Previously “{prev_j}”"))
            prev_name, prev_j = h["subsidiary_name"], h["jurisdiction_raw"]
    tl = pd.DataFrame(rows)
    order = {"Listed every year": 0}
    tl["_o"] = tl["status"].map(lambda s: order.get(s, 1))
    tl = tl.sort_values(["parent_ticker", "_o", "subsidiary_name (latest)"]).drop(columns="_o")
    ev = pd.DataFrame(events, columns=["parent_ticker", "fiscal_year", "change", "subsidiary_name",
                                       "jurisdiction (as filed)", "detail"])
    ev = ev.sort_values(["parent_ticker", "fiscal_year", "change", "subsidiary_name"])
    return tl.reset_index(drop=True), ev.reset_index(drop=True)


def graph_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Edges ready for a graph store / Graph RAG: one row per (owner, subsidiary, year)."""
    e = pd.DataFrame({
        "source": df["immediate_parent"], "target": df["subsidiary_name"],
        "relation": "HAS_SUBSIDIARY", "fiscal_year": df["fiscal_year"],
        "target_jurisdiction": df["jurisdiction_raw"], "target_country": df["country"],
        "ownership_pct": df["ownership_pct"], "evidence": "Exhibit 21 (" + df["accession"] + ")",
        "source_url": df["source_url"], "confidence": "filed",
    })
    j = pd.DataFrame({
        "source": df["subsidiary_name"], "target": df["country"],
        "relation": "INCORPORATED_IN", "fiscal_year": df["fiscal_year"],
        "target_jurisdiction": df["jurisdiction_raw"], "target_country": df["country"],
        "ownership_pct": None, "evidence": "Exhibit 21 (" + df["accession"] + ")",
        "source_url": df["source_url"], "confidence": "filed",
    })
    return pd.concat([e, j], ignore_index=True)


def run(manifest: Path, out_dir: Path) -> dict:
    """Write the two outputs: the Excel report (for people) and graph_edges.csv (for Graph RAG)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    df, qa = load_records(manifest)
    df = match_entities(df)
    tl, ev = timeline(df)
    from .report import write_workbook
    report = out_dir / "parent_subsidiary_report.xlsx"
    edges = out_dir / "graph_edges.csv"
    write_workbook(report, df, tl, ev, pd.DataFrame(qa))
    graph_edges(df).to_csv(edges, index=False)
    return {
        "filings": len(qa), "subsidiary_rows": len(df), "distinct_subsidiaries": len(tl),
        "year_over_year_changes": len(ev), "report": str(report), "graph_edges": str(edges),
        "files_needing_review": [q["source_file"] for q in qa if not str(q["status"]).startswith("OK")],
    }
