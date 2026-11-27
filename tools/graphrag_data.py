"""
GraphRAG data access: find filings, inspect their sections, batch-export section text,
and rebuild the extraction pipeline's filing list.

Read-only. Reads the Data team's `finance` views with the settings in .env.aws,
so run it from the folder that holds .env.aws.
Setup: see the doc "Connecting to the Data Team's Database from Python".

Run with uv:   uv run graphrag_data.py <command> [options]
Run with pip:  python graphrag_data.py <command> [options]   (with your venv activated)

Commands
    filings    list matching filings: form, fiscal year/period, accession, section count
    inventory  list their sections without text (fast; check before exporting)
    fetch      export section text: data/sections/<TICKER>_<accession>.jsonl
               (one line per section) plus data/manifest_<UTC time>.csv
    config     build extraction_pipeline/config/filings.yaml: every 10-K and 10-Q whose
               validation_status is "passed" and that has sections, each with
               `status: passed`; companies in ON_HOLD get all their filings listed with
               `status: on_hold` (the pipeline skips those). With no --tickers /
               --ciks / --accessions it uses PIPELINE_TICKERS below. Prints only, unless
               --write (repo path) or --out FILE.

Options (all commands; filings/inventory/fetch need --tickers, --ciks or --accessions)
    --tickers AAPL MSFT JPM          companies, any case
    --ciks 0001652044                companies by CIK, with or without leading zeros;
                                     for companies the database has no ticker for yet
    --accessions 0000320193-25-000079  exact filings; use these for reproducible runs
    --form 10-K | 10-Q               leave out for both
    --since / --until YYYY-MM-DD     filing-date range
    --latest                         each company's newest matching filing only
    --section-types risk_factors mda business market_risk financial_statements

Examples
    uv run graphrag_data.py filings --tickers AAPL
    uv run graphrag_data.py filings --ciks 1652044
    uv run graphrag_data.py inventory --tickers AAPL MSFT JPM --form 10-K --latest
    uv run graphrag_data.py fetch --tickers AAPL MSFT JPM --form 10-K --latest --section-types risk_factors
    uv run graphrag_data.py config                  # print the pipeline filing list
    uv run graphrag_data.py config --write          # overwrite ../extraction_pipeline/config/filings.yaml

Notes
    - Tickers and CIKs select companies together: --tickers AAPL --ciks 1652044 means both.
    - Each exported record carries `content` (the section text; paragraphs are separated
      by a blank line, "\\n\\n") and provenance fields. Anchor extractions on
      accession_number + section_key, not on the UUIDs, which change if the database is rebuilt.
    - fiscal_year / fiscal_period are derived here, not supplied by the Data team;
      unusual fiscal calendars can come out one year off.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(".env.aws")

OUT_DIR = Path("data")
FILINGS_CONFIG = Path(__file__).resolve().parent.parent / "extraction_pipeline" / "config" / "filings.yaml"

# Companies in the extraction pipeline (the `config` command's default).
PIPELINE_TICKERS = [
    "AAPL", "CASY", "MCK", "MDT", "MSFT", "MU", "NKE", "NTAP", "PH", "TTWO",
    "AMZN", "CAH", "GOOGL", "INTC", "WDC", "WMT",
]

# Companies listed in filings.yaml but not run yet (ticker -> why). `config` writes every
# 10-K/10-Q of theirs with `status: on_hold`, whatever its validation_status, and the
# pipeline skips those rows. Remove an entry once the Data team has fixed it, then rerun
# `config --write`. Check them meanwhile with: graphrag_data.py filings --tickers INTC CAH
ON_HOLD = {
    "INTC": "Data team still fixing its filings",
    "CAH": "Data team still fixing its filings",
}

# Companies the database has no ticker for yet (CIK -> ticker). Asking for the ticker also
# selects the CIK, and the ticker is filled in on every row, so file names stay
# <TICKER>_<accession>. Remove an entry once the Data team sets the ticker.
MISSING_TICKERS = {
    "0001652044": "GOOGL",  # Alphabet Inc.
}


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #

FILINGS_SQL = """
    SELECT f.filing_id, f.ticker, lpad(f.cik, 10, '0') AS cik, f.company_name, f.form,
           f.accession_number, f.filing_date, f.reporting_period_end,
           f.processing_set_id, f.set_key, f.selection_reason, f.validation_status,
           (SELECT count(*) FROM finance.filing_section s
             WHERE s.filing_id = f.filing_id
               AND s.processing_set_id = f.processing_set_id) AS n_sections,
           -- fiscal-year-end month, taken from the company's latest 10-K
           -- (minus 10 days so a 52/53-week year ending Oct 1 or Jan 2 counts as Sep / Dec)
           (SELECT extract(month FROM k.reporting_period_end - 10)::int
              FROM finance.filing k
             WHERE k.company_id = f.company_id AND k.form = '10-K'
               AND k.reporting_period_end IS NOT NULL
             ORDER BY k.filing_date DESC LIMIT 1) AS fye_month
    FROM finance.filing f
    WHERE ((%(tickers)s::text[] IS NULL AND %(ciks)s::text[] IS NULL)
           OR f.ticker = ANY(%(tickers)s::text[])
           OR lpad(f.cik, 10, '0') = ANY(%(ciks)s::text[]))
      AND (%(accessions)s::text[] IS NULL OR f.accession_number = ANY(%(accessions)s::text[]))
      AND (%(form)s::text         IS NULL OR f.form = %(form)s::text)
      AND (%(since)s::date        IS NULL OR f.filing_date >= %(since)s::date)
      AND (%(until)s::date        IS NULL OR f.filing_date <= %(until)s::date)
    ORDER BY f.ticker, f.filing_date DESC
"""

# Section metadata only: cheap, used to check coverage before pulling text.
INVENTORY_SQL = """
    SELECT f.ticker, lpad(f.cik, 10, '0') AS cik, f.form, f.accession_number, f.filing_date,
           s.section_id, s.section_key, s.section_order, s.part, s.item,
           s.section_type, s.original_title, s.section_validation_status,
           s.offset_end - s.offset_start AS n_chars
    FROM finance.filing f
    JOIN finance.filing_section s
      ON s.filing_id = f.filing_id
     AND s.processing_set_id = f.processing_set_id
    WHERE f.filing_id = ANY(%(filing_ids)s::uuid[])
      AND (%(section_types)s::text[] IS NULL OR s.section_type = ANY(%(section_types)s::text[]))
    ORDER BY f.ticker, f.filing_date DESC, s.section_order
"""

# Full section records, including text, for one filing at a time.
SECTIONS_SQL = """
    SELECT f.accession_number, f.ticker, lpad(f.cik, 10, '0') AS cik, f.company_name, f.form,
           f.filing_date, f.reporting_period_end,
           f.processing_set_id, f.set_key,
           s.report_id, s.section_id, s.section_key, s.section_order,
           s.part, s.item, s.section_type, s.original_title,
           s.offset_start, s.offset_end, s.source_locator,
           s.section_validation_status, s.extraction_version,
           s.source_url, s.source_sha256, s.content
    FROM finance.filing f
    JOIN finance.filing_section s
      ON s.filing_id = f.filing_id
     AND s.processing_set_id = f.processing_set_id
    WHERE f.filing_id = %(filing_id)s::uuid
      AND (%(section_types)s::text[] IS NULL OR s.section_type = ANY(%(section_types)s::text[]))
    ORDER BY s.section_order
"""


# --------------------------------------------------------------------------- #
# Functions (import these from a notebook or the extraction pipeline)
# --------------------------------------------------------------------------- #

def connect() -> psycopg.Connection:
    conn = psycopg.connect(row_factory=dict_row)
    conn.read_only = True  # every transaction on this connection is read-only
    return conn


def normalise_cik(cik) -> str:
    """'1652044', 1652044 or 'CIK0001652044' -> '0001652044' (the SEC's 10-digit form)."""
    return str(cik).strip().upper().removeprefix("CIK").zfill(10)


def _fill_ticker(ticker, cik):
    """The database's ticker, or the MISSING_TICKERS one when the database has none."""
    if isinstance(ticker, str) and ticker or cik is None or pd.isna(cik):
        return ticker
    return MISSING_TICKERS.get(normalise_cik(cik))


def find_filings(conn, tickers=None, accessions=None, form=None, since=None, until=None,
                 ciks=None) -> pd.DataFrame:
    """
    Step 1:
    - which filings exist;
    - which version is selected;
    - how many sections each has.
    Companies are matched by ticker OR CIK.
    """
    tickers = [t.upper() for t in tickers] if tickers else []   # "aapl" -> "AAPL"
    ciks = [normalise_cik(c) for c in ciks] if ciks else []
    # a ticker the database does not hold yet is looked up by its CIK
    ciks += [c for c, t in MISSING_TICKERS.items() if t in tickers and c not in ciks]
    form = form.upper() if form else None
    params = {"tickers": tickers or None, "ciks": ciks or None, "accessions": accessions,
              "form": form, "since": since, "until": until}
    df = pd.DataFrame(conn.execute(FILINGS_SQL, params).fetchall())
    if not df.empty:
        df["ticker"] = [_fill_ticker(t, c) for t, c in zip(df["ticker"], df["cik"])]
        df[["fiscal_year", "fiscal_period"]] = df.apply(_fiscal_label, axis=1, result_type="expand")
        df["fiscal_year"] = df["fiscal_year"].astype("Int64")
    return df


def _fiscal_label(row) -> tuple:
    """Estimate fiscal year and period from the period-end date and the fiscal-year-end month.

    The Data team does not supply fiscal_year / fiscal_period yet (data contract item 4),
    so this is derived. Fiscal year = calendar year in which that fiscal year ends,
    e.g. Apple's quarter ending Dec 2024 is FY2025 Q1.
    """
    end, fye = row["reporting_period_end"], row["fye_month"]
    if end is None or pd.isna(fye):
        return (None, None)
    end = pd.Timestamp(end) - pd.Timedelta(days=10)
    months_after_fye = (end.month - int(fye)) % 12          # 0 = the fiscal-year-end month
    fiscal_year = end.year + (1 if end.month > int(fye) else 0)
    if row["form"] == "10-K":
        return (fiscal_year, "FY")
    return (fiscal_year, f"Q{round(months_after_fye / 3)}")


def section_inventory(conn, filing_ids, section_types=None) -> pd.DataFrame:
    """
    Step 2: section metadata (no text) for the chosen filings.
    """
    params = {"filing_ids": [str(i) for i in filing_ids], "section_types": section_types}
    df = pd.DataFrame(conn.execute(INVENTORY_SQL, params).fetchall())
    if not df.empty:
        df["ticker"] = [_fill_ticker(t, c) for t, c in zip(df["ticker"], df["cik"])]
    return df


def fetch_sections(conn, filing_id, section_types=None) -> list[dict]:
    """
    Step 3: full section records, with text, for one filing.
    """
    params = {"filing_id": str(filing_id), "section_types": section_types}
    rows = conn.execute(SECTIONS_SQL, params).fetchall()
    for r in rows:
        r["ticker"] = _fill_ticker(r["ticker"], r["cik"])
    return rows


def export_sections(conn, filings: pd.DataFrame, section_types=None, out_dir: Path = OUT_DIR) -> pd.DataFrame:
    """
    Write one JSONL per filing plus a manifest of what was exported and from which version.
    """
    sec_dir = out_dir / "sections"
    sec_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for f in filings.itertuples():
        rows = fetch_sections(conn, f.filing_id, section_types)
        path = sec_dir / f"{f.ticker or f.cik}_{f.accession_number}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                r["n_chars"] = len(r["content"] or "")
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        manifest.append({
            "ticker": f.ticker, "accession_number": f.accession_number, "form": f.form,
            "filing_date": f.filing_date, "processing_set_id": f.processing_set_id,
            "set_key": f.set_key, "n_sections": len(rows), "file": str(path),
        })
        print(f"  {f.ticker} {f.form} {f.accession_number}: {len(rows)} sections -> {path}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_df = pd.DataFrame(manifest)
    manifest_path = out_dir / f"manifest_{stamp}.csv"
    manifest_df.to_csv(manifest_path, index=False)
    print(f"Manifest -> {manifest_path}")
    return manifest_df


def filings_config(filings: pd.DataFrame, companies: list[str], since=None, until=None) -> str:
    """
    Text of extraction_pipeline/config/filings.yaml for the given find_filings() result.

    Lists every 10-K and 10-Q whose validation_status is "passed" and that has sections
    (status: passed), plus every 10-K and 10-Q of the ON_HOLD companies (status: on_hold).
    The rest are listed in the header comment, so the exclusions stay visible.
    `companies` is the list asked for (tickers / CIKs); any with no filing is listed too.
    """
    f = filings[filings.form.isin(["10-K", "10-Q"])].copy()
    if f.empty:
        raise SystemExit("No 10-K or 10-Q filings found for those companies.")
    f["rpe"] = f.reporting_period_end.astype(str)
    f["label"] = f.ticker.fillna(f.cik)
    held = f.label.isin(ON_HOLD)
    f["status"] = held.map({True: "on_hold", False: "passed"})

    keep = f[held | ((f.validation_status == "passed") & (f.n_sections > 0))]
    left_out = f.drop(keep.index)
    keep = keep.sort_values(["label", "rpe", "accession_number"])
    held_labels = sorted(set(keep.label[keep.status == "on_hold"]))
    found = set(f.label) | set(f.cik)
    not_found = [c for c in companies if c not in found]

    window = ""
    if since:
        window += f", filed on or after {since}"
    if until:
        window += f", filed on or before {until}"
    labels = sorted(set(keep.label))
    head = [
        f"# Filings for the {len(labels)} pipeline companies: every 10-K and 10-Q loaded in the",
        f"# Data team database on {date.today().isoformat()} whose validation_status is \"passed\"{window}",
        "# (status: passed), plus every 10-K and 10-Q of the on-hold companies (status: on_hold).",
        f"# Companies: {', '.join(labels)}.",
        "# Generated by `uv run graphrag_data.py config --write` (run in tools/);",
        "# rerun it rather than editing by hand.",
    ]
    if held_labels:
        head.append("# status: on_hold = listed but not run yet; the pipeline skips these rows")
        head.append("# (ON_HOLD in graphrag_data.py):")
        head += [f"#   {c}: {ON_HOLD[c]}" for c in held_labels]
    if not_found:
        head.append(f"# No filings in the database for: {', '.join(not_found)}.")
    if left_out.empty:
        head.append("# Left out: none.")
    else:
        head.append("# Left out:")
        for r in left_out.sort_values(["label", "rpe"]).itertuples():
            why = r.validation_status if r.n_sections > 0 else f"{r.validation_status}, no sections"
            head.append(f"#   {r.label} {r.form} {r.accession_number} ({why})")
    head += [
        "# The fetch step exports each one to <data_dir>/sections/<TICKER>_<accession>.jsonl",
        "# (one line per section). Within a company, filings are in period order.",
        "",
        "filings:",
    ]

    rows = []
    for r in keep.itertuples():
        t = f"{r.label},".ljust(5)
        rows.append(
            f'  - {{ticker: {t} cik: "{r.cik}", form: {r.form}, '
            f'reporting_period_end: "{r.rpe}", accession: "{r.accession_number}", status: {r.status}}}'
        )

    summary = keep.groupby(["label", "status", "form"]).size().unstack(fill_value=0)
    print(summary.to_string(), file=sys.stderr)
    n_held = int((keep.status == "on_hold").sum())
    print(f"\n{len(keep)} filings listed ({len(keep) - n_held} passed, {n_held} on_hold), "
          f"{len(left_out)} left out.", file=sys.stderr)
    if not_found:
        print(f"No filings found for: {', '.join(not_found)}", file=sys.stderr)
    print(file=sys.stderr)
    return "\n".join(head + rows) + "\n"


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["filings", "inventory", "fetch", "config"])
    p.add_argument("--tickers", nargs="+", help="e.g. AAPL MSFT JPM")
    p.add_argument("--ciks", nargs="+", help="e.g. 0001652044 (leading zeros optional)")
    p.add_argument("--accessions", nargs="+", help="exact accession numbers; combine with other filters")
    p.add_argument("--form", type=str.upper, choices=["10-K", "10-Q"])
    p.add_argument("--since", help="filing date from, YYYY-MM-DD")
    p.add_argument("--until", help="filing date to, YYYY-MM-DD")
    p.add_argument("--section-types", nargs="+", help="e.g. risk_factors mda business")
    p.add_argument("--latest", action="store_true", help="keep only each company's most recent matching filing")
    p.add_argument("--write", action="store_true", help=f"config only: overwrite {FILINGS_CONFIG}")
    p.add_argument("--out", type=Path, help="config only: write to this file instead")
    a = p.parse_args()

    if a.command == "config" and not (a.tickers or a.ciks or a.accessions):
        a.tickers = PIPELINE_TICKERS
    if not (a.tickers or a.ciks or a.accessions):
        sys.exit("Give --tickers, --ciks or --accessions so we never pull the whole database by accident.")
    if (a.write or a.out) and a.command != "config":
        sys.exit("--write and --out only apply to the config command.")

    with connect() as conn:
        filings = find_filings(conn, a.tickers, a.accessions, a.form, a.since, a.until, ciks=a.ciks)
        if filings.empty:
            sys.exit("No matching filings are loaded yet.")
        if a.latest:
            filings = filings.groupby("cik", sort=False).head(1)

        empty = filings[filings.n_sections == 0]
        if not empty.empty:
            reasons = ", ".join(str(r) for r in empty.selection_reason.unique())
            print(f"Note: {len(empty)} filing(s) have no sections yet (selection_reason: {reasons}).",
                  file=sys.stderr)

        if a.command == "filings":
            cols = ["ticker", "cik", "form", "fiscal_year", "fiscal_period", "reporting_period_end",
                    "filing_date", "accession_number", "n_sections"]
            print(filings[cols].to_string(index=False))

        elif a.command == "inventory":
            inv = section_inventory(conn, filings.filing_id, a.section_types)
            cols = ["ticker", "accession_number", "section_order", "item",
                    "section_type", "original_title", "n_chars", "section_validation_status"]
            print(inv[cols].to_string(index=False) if not inv.empty else "No sections match.")

        elif a.command == "fetch":
            export_sections(conn, filings[filings.n_sections > 0], a.section_types)

        elif a.command == "config":
            companies = [t.upper() for t in a.tickers or []] + [normalise_cik(c) for c in a.ciks or []]
            text = filings_config(filings, companies, a.since, a.until)
            out = a.out or (FILINGS_CONFIG if a.write else None)
            if out is None:
                print(text)
            else:
                if not out.parent.is_dir():
                    sys.exit(f"{out.parent} does not exist; outside the repo, pass --out FILE.")
                out.write_text(text, encoding="utf-8")
                print(f"Wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
