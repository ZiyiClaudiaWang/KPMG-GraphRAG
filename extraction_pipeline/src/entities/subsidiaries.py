"""Step 6a - Subsidiaries: the Exhibit 21 subsidiary list of every 10-K in config/filings.yaml.

Input : the form: 10-K rows of config/filings.yaml (ticker, cik, accession,
        reporting_period_end); 10-Q rows are skipped, since only a 10-K carries Exhibit 21.
        Each exhibit is downloaded from SEC EDGAR once into data/ex21/raw/ (listed in
        data/ex21/manifest.csv); later runs read the saved copy and need no network.
Output: data/output/subsidiaries.jsonl        one row per subsidiary per 10-K (loaded by step 10)
        data/checks/subsidiaries/subsidiary_qa.csv         one row per 10-K: rows parsed, and what to check
        data/output/subsidiary_catalog.json   the same subsidiaries in entity_annotation.py's
                                              catalog format, for linking Claims; step 6b
                                              (entity_catalog.py) copies it into data/entity_catalog.json.
                                              Each filing is offered the subsidiaries of its own
                                              year's 10-K, plus those listed only in the 10-K
                                              before it, flagged (catalog()).
        data/checks/subsidiaries/subsidiary_renames.csv    possible renames between consecutive 10-Ks, for review

How Exhibit 21 is read lives in src/entities/ex21/ (edgar.py downloads, parse.py reads the exhibit,
normalize.py maps jurisdictions and builds the identity keys). This file only runs the step.

No OpenAI calls and no Data team export; nothing here changes a label request hash.
Downloading needs SEC_USER_AGENT="Name email" in the environment or extraction_pipeline/.env.
A 10-K that cannot be downloaded is a CHECK row in subsidiary_qa.csv, and the run goes on.

Keep config/filings.yaml current: the outputs cover exactly the 10-Ks listed there.

Usage:
  python run_pipeline.py --steps subsidiaries
  python -m src.subsidiaries
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from ..common import CONFIG_DIR, Paths, filings, load_env, log, read_csv, read_jsonl, settings, write_csv, write_jsonl
from .ex21 import edgar
from .ex21.normalize import entity_key, is_registrant, normalised_name, normalize_jurisdiction, sub_id
from .ex21.parse import assign_parents, parse_file

STEP = "subsidiaries"
PARSER_VERSION = "ex21_v4"

MANIFEST_COLS = ["ticker", "cik", "accession", "reporting_period_end", "registrant_name",
                 "source_url", "local_file"]
QA_COLS = ["ticker", "accession", "reporting_period_end", "local_file", "rows_parsed",
           "duplicate_names", "missing_jurisdiction", "unmapped_jurisdiction",
           "registrant_rows_dropped", "multi_level", "parse_method", "status"]


def reporting_period_end(filing: dict) -> str:
    """The filing's reporting_period_end from filings.yaml."""
    return str(filing["reporting_period_end"])


def ten_ks() -> list[dict]:
    rows = []
    for f in filings():
        if str(f.get("form", "")).upper() != "10-K":
            continue
        rows.append({"ticker": f["ticker"].upper(), "cik": str(f["cik"]).zfill(10),
                     "accession": f["accession"], "reporting_period_end": reporting_period_end(f)})
    return rows


BY_REFERENCE = "BY_REFERENCE"     # a known 10-K with no EX-21 attached: not a problem


def _check(target: dict, status: str) -> dict:
    return {**target, "local_file": "", "rows_parsed": 0, "status": status}


def fetch(targets: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Manifest rows by accession (downloading what is not saved yet), and CHECK rows for the
    10-Ks with nothing to read. The manifest keeps 10-Ks no longer in filings.yaml; they are
    simply not used."""
    saved = {r["accession"]: r for r in read_csv(Paths.ex21_manifest())} \
        if Paths.ex21_manifest().exists() else {}
    checks = []
    by_reference = (settings().get("subsidiaries") or {}).get("ex21_by_reference") or {}
    for t in targets:
        row = saved.get(t["accession"])
        if row and (Paths.ex21_raw().parent / row["local_file"]).exists():
            continue
        if t["accession"] in by_reference:
            checks.append(_check(t, f"{BY_REFERENCE}: EX-21 incorporated by reference to 10-K "
                                    f"{by_reference[t['accession']]} (settings.yaml subsidiaries)"))
            continue
        load_env()
        if not edgar.user_agent():
            checks.append(_check(t, "CHECK: not downloaded; set SEC_USER_AGENT='Name email'"))
            log(STEP, f"{t['ticker']} {t['accession']}: not saved yet and SEC_USER_AGENT is not set; skipped")
            continue
        try:
            url = edgar.ex21_url(t["cik"], t["accession"])
            content = edgar.get(url) if url else b""
            registrant = edgar.registrant_name(t["cik"]) if url else ""
        except Exception as e:             # offline, or SEC refused: the rest of the run goes on
            checks.append(_check(t, f"CHECK: download failed ({type(e).__name__}: {e})"))
            log(STEP, f"{t['ticker']} {t['accession']}: download failed: {e}")
            continue
        if not url:
            checks.append(_check(t, "CHECK: no EX-21 attached to this 10-K (incorporated by reference?)"))
            log(STEP, f"{t['ticker']} {t['accession']}: no EX-21 attached")
            continue
        local = f"raw/{t['ticker']}_{t['accession']}_ex21{Path(url).suffix or '.htm'}"
        Paths.ex21_raw().mkdir(parents=True, exist_ok=True)
        (Paths.ex21_raw().parent / local).write_bytes(content)
        saved[t["accession"]] = {**t, "registrant_name": registrant, "source_url": url, "local_file": local}
        log(STEP, f"{t['ticker']} {t['accession']}: downloaded {local}")
    write_csv(Paths.ex21_manifest(), list(saved.values()), MANIFEST_COLS)
    return saved, checks


def aliases() -> dict:
    """config/subsidiary_aliases.yaml (or the file named by PIPELINE_SUBSIDIARY_ALIASES, for tests):

    {"same_entity": {(ticker, normalised other spelling): name to use},
     "distinct": {(ticker, frozenset of the two normalised names)}}
    """
    path = Path(os.environ.get("PIPELINE_SUBSIDIARY_ALIASES") or CONFIG_DIR / "subsidiary_aliases.yaml")
    cfg = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    same, distinct = {}, set()
    for e in cfg.get("same_entity") or []:
        ticker = e["ticker"].upper()
        for other in e["also"]:
            key = (ticker, normalised_name(other))
            if key in same and same[key] != e["name"]:
                raise ValueError(f"subsidiary_aliases: {other!r} ({ticker}) is listed under two names")
            same[key] = e["name"]
    for e in cfg.get("distinct") or []:
        a, b = e["names"]
        distinct.add((e["ticker"].upper(), frozenset({normalised_name(a), normalised_name(b)})))
    return {"same_entity": same, "distinct": distinct}


def parse(target: dict, entry: dict, alias: dict | None = None) -> tuple[list[dict], dict]:
    """Subsidiary rows of one 10-K, and its QA row."""
    parsed = parse_file(Paths.ex21_raw().parent / entry["local_file"])
    # An earlier manifest called the EDGAR name parent_name.
    registrant = entry.get("registrant_name") or entry.get("parent_name") or ""
    kept = [r for r in parsed if not is_registrant(r["subsidiary_name"], registrant)]
    same = (alias or {}).get("same_entity", {})
    rows = []
    for r in assign_parents(kept, target["ticker"]):
        country, region = normalize_jurisdiction(r["jurisdiction_raw"])
        # A reviewed other spelling takes the sub_id (and keys) of the name it stands for;
        # "name" stays as filed, so the OWNS edge records what this year's exhibit says.
        canonical = same.get((target["ticker"].upper(), normalised_name(r["subsidiary_name"])))
        key_name = canonical or r["subsidiary_name"]
        flags = r["flags"]
        if canonical:
            flags = ";".join(f for f in [flags, "reviewed_alias"] if f)
        rows.append({
            "sub_id": sub_id(target["cik"], key_name),
            "name": r["subsidiary_name"],
            "normalised_name": normalised_name(key_name),
            "entity_key": entity_key(key_name),
            "parent_cik": target["cik"], "parent_ticker": target["ticker"],
            "accession_number": target["accession"],
            "reporting_period_end": target["reporting_period_end"],
            "jurisdiction_raw": r["jurisdiction_raw"], "country": country, "state_or_region": region,
            "ownership_pct": r["ownership_pct"], "level": r["level"],
            "immediate_parent": r["immediate_parent"], "relationship": r["relationship"],
            "row_order": r["row_order"], "parse_method": r["parse_method"], "flags": flags,
            "source_url": entry["source_url"], "source_file": entry["local_file"],
            "parser_version": PARSER_VERSION,
        })
    
    seen, unique = {}, []
    for r in rows:
        if r["sub_id"] in seen:
            first = seen[r["sub_id"]]
            first["flags"] = ";".join(f for f in [first["flags"], f"also_listed_as_row_{r['row_order']}"] if f)
            continue
        seen[r["sub_id"]] = r
        unique.append(r)

    names = [r["normalised_name"] for r in rows]
    unmapped = sorted({r["jurisdiction_raw"] for r in rows if r["jurisdiction_raw"] and not r["country"]})
    status = "OK" if rows else "CHECK: no rows parsed (image/PDF exhibit or unusual layout?)"
    if rows and unmapped:
        status = "CHECK: jurisdiction not mapped to a country: " + "; ".join(unmapped)
    qa = {**target, "local_file": entry["local_file"], "rows_parsed": len(rows),
          "duplicate_names": len(names) - len(set(names)),
          "missing_jurisdiction": sum(1 for r in rows if not r["jurisdiction_raw"]),
          "unmapped_jurisdiction": sum(1 for r in rows if r["jurisdiction_raw"] and not r["country"]),
          "registrant_rows_dropped": len(parsed) - len(kept),
          "multi_level": int(any(r["level"] > 0 for r in rows)),
          "parse_method": ",".join(sorted({r["parse_method"] for r in rows})) or "NONE",
          "status": status}
    return unique, qa


RENAME_COLS = ["parent_ticker", "parent_cik", "earlier_10k", "later_10k", "entity_key",
               "earlier_name", "later_name", "earlier_sub_id", "later_sub_id", "review"]


def reference_10ks(rows: list[dict]) -> dict[str, tuple[str, str | None]]:
    """For every filing in filings.yaml: (its reference 10-K, the 10-K before that one).

    The reference 10-K is the first 10-K of the same parent whose reporting_period_end is on or
    after the filing's (so a 10-Q takes the 10-K of its own fiscal year, and a 10-K takes
    itself); a filing after the parent's last 10-K takes that last one. Only 10-Ks whose
    Exhibit 21 was parsed count, so a year with no exhibit falls through to the next one.
    """
    listed: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        pair = (r["reporting_period_end"], r["accession_number"])
        if pair not in listed.setdefault(r["parent_cik"], []):
            listed[r["parent_cik"]].append(pair)
    out = {}
    for f in filings():
        ten_ks = sorted(listed.get(str(f["cik"]).zfill(10), []))
        if not ten_ks:
            continue
        period = str(f["reporting_period_end"])
        i = next((k for k, (end, _) in enumerate(ten_ks) if end >= period), len(ten_ks) - 1)
        out[f["accession"]] = (ten_ks[i][1], ten_ks[i - 1][1] if i > 0 else None)
    return out


def catalog(rows: list[dict]) -> list[dict]:
    """Entity-catalog rows (entity_annotation.validate_catalog format), one per sub_id.

    A subsidiary applies to a filing when the filing's reference 10-K lists it (see
    reference_10ks), so a 2021 10-Q is not offered a company first listed in 2024. A
    subsidiary listed in the 10-K before the reference one but not in the reference one
    (sold or merged during the year) still applies, and the filing is named in
    prior_year_only_accessions so a link through it can be flagged.

    Everything is needs_review: nothing here is checked by a person yet, so step 9 would
    report a hit as candidate_match_needs_review, not matched.
    """
    listed_in: dict[str, set[str]] = {}
    for r in rows:
        listed_in.setdefault(r["sub_id"], set()).add(r["accession_number"])
    refs = reference_10ks(rows)
    out: dict[str, dict] = {}
    for r in rows:
        entry = out.setdefault(r["sub_id"], {
            "entity_id": r["sub_id"], "entity_type": "subsidiary", "issuer_cik": r["parent_cik"],
            "name": r["name"], "aliases": [], "accessions": [], "prior_year_only_accessions": [],
            "listed_in_10ks": [], "review_status": "needs_review", "source": "exhibit_21",
        })
        if r["name"] != entry["name"] and r["name"] not in entry["aliases"]:
            entry["aliases"].append(r["name"])
    for f in filings():
        ref = refs.get(f["accession"])
        if not ref:
            continue
        current, prior = ref
        for sub_id, tens in listed_in.items():
            entry = out[sub_id]
            if entry["issuer_cik"] != str(f["cik"]).zfill(10):
                continue
            if current in tens:
                entry["accessions"].append(f["accession"])
            elif prior in tens:
                entry["accessions"].append(f["accession"])
                entry["prior_year_only_accessions"].append(f["accession"])
    for sub_id, entry in out.items():
        entry["listed_in_10ks"] = sorted(listed_in[sub_id])
    # validate_catalog needs a filing scope on every entry; one whose 10-K is no longer in
    # filings.yaml has none and is left out.
    return [e for e in out.values() if e["accessions"]]


def _key_name(row: dict) -> str:
    return row.get("normalised_name") or normalised_name(row["name"])


def rename_candidates(rows: list[dict], alias: dict | None = None) -> list[dict]:
    """Pairs that look like one subsidiary renamed between two consecutive 10-Ks.

    Same parent and same entity_key (legal form stripped), the earlier sub_id gone from the
    later 10-K and the later one new in it: "Example Holdings Inc." -> "Example Holdings LLC".
    Reported for review only; sub_ids are never merged here. A pair already checked and kept
    apart in config/subsidiary_aliases.yaml (distinct) gets review=distinct; the rest are
    left blank, still to review. A pair merged there (same_entity) has one sub_id, so it no
    longer shows up at all.
    """
    distinct = (alias or {}).get("distinct", set())
    by_parent: dict[str, dict[tuple[str, str], dict[str, dict]]] = {}
    for r in rows:
        key = (r["reporting_period_end"], r["accession_number"])
        by_parent.setdefault(r["parent_cik"], {}).setdefault(key, {})[r["sub_id"]] = r
    out = []
    for cik, years in by_parent.items():
        ordered = sorted(years)
        for (a_key, b_key) in zip(ordered, ordered[1:]):
            a, b = years[a_key], years[b_key]
            gone = [a[s] for s in a if s not in b]
            new = {}
            for s in b:
                if s not in a:
                    new.setdefault(b[s]["entity_key"], []).append(b[s])
            for old in gone:
                for later in new.get(old["entity_key"], []):
                    out.append({"parent_ticker": old["parent_ticker"], "parent_cik": cik,
                                "earlier_10k": a_key[1], "later_10k": b_key[1],
                                "entity_key": old["entity_key"], "earlier_name": old["name"],
                                "later_name": later["name"], "earlier_sub_id": old["sub_id"],
                                "later_sub_id": later["sub_id"],
                                "review": "distinct" if (old["parent_ticker"].upper(), frozenset(
                                    {_key_name(old), _key_name(later)})) in distinct else ""})
    return out


def run() -> None:
    # Oldest 10-K first per parent: step 10 sets a Subsidiary node's name from each row in turn,
    # so the node ends up with the latest 10-K's spelling whatever order filings.yaml is in.
    targets = sorted(ten_ks(), key=lambda t: (t["cik"], t["reporting_period_end"]))
    if not targets:
        log(STEP, "no form: 10-K rows in filings.yaml; nothing to do")
        return
    saved, qa_rows = fetch(targets)
    skipped = {q["accession"] for q in qa_rows}
    alias = aliases()
    rows = []
    for t in targets:
        if t["accession"] in skipped or t["accession"] not in saved:
            continue
        found, qa = parse(t, saved[t["accession"]], alias)
        rows += found
        qa_rows.append(qa)
    write_jsonl(Paths.subsidiaries(), rows)
    write_csv(Paths.subsidiary_qa(), qa_rows, QA_COLS)
    entries = catalog(rows)
    Paths.subsidiary_catalog().write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    seen_names = {(r["parent_ticker"].upper(), normalised_name(r["name"])) for r in rows}
    unused = sorted(f"{t}: {n}" for t, n in alias["same_entity"] if (t, n) not in seen_names)
    if unused:
        log(STEP, f"subsidiary_aliases.yaml: {len(unused)} spelling(s) found in no 10-K: {unused}")
    merged = sum(1 for r in rows if "reviewed_alias" in r["flags"])
    if merged:
        log(STEP, f"{merged} rows took a reviewed name from config/subsidiary_aliases.yaml")
    renames = rename_candidates(rows, alias)
    write_csv(Paths.subsidiary_renames(), renames, RENAME_COLS)
    if renames:
        open_ = sum(1 for r in renames if not r["review"])
        log(STEP, f"{len(renames)} possible renames between years ({open_} not reviewed yet) "
                  f"-> {Paths.subsidiary_renames()}")
    log(STEP, f"{len(targets)} 10-Ks -> {len(rows)} subsidiary rows, {len(entries)} distinct "
              f"subsidiaries -> {Paths.subsidiaries()}")
    for q in qa_rows:
        if q["status"] != "OK":
            log(STEP, f"CHECK {q['ticker']} {q['accession']}: {q['status']}")


def read_rows() -> list[dict]:
    return list(read_jsonl(Paths.subsidiaries())) if Paths.subsidiaries().exists() else []


if __name__ == "__main__":
    run()
