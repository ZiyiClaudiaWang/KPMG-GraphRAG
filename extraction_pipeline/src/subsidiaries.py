"""Step 1b - Subsidiaries: the Exhibit 21 subsidiary list of every 10-K in config/filings.yaml.

Input : the form: 10-K rows of config/filings.yaml (ticker, cik, accession,
        reporting_period_end); 10-Q rows are skipped, since only a 10-K carries Exhibit 21.
        Each exhibit is downloaded from SEC EDGAR once into data/ex21/raw/ (listed in
        data/ex21/manifest.csv); later runs read the saved copy and need no network.
Output: data/output/subsidiaries.jsonl        one row per subsidiary per 10-K (loaded by step 8)
        data/output/subsidiary_qa.csv         one row per 10-K: rows parsed, and what to check
        data/output/subsidiary_catalog.json   the same subsidiaries in entity_annotation.py's
                                              catalog format, for linking Claims later. No step
                                              reads it yet (step 7b reads data/entity_catalog.json).

How Exhibit 21 is read lives in src/ex21/ (edgar.py downloads, parse.py reads the exhibit,
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
from pathlib import Path

from .common import Paths, filings, load_env, log, read_csv, read_jsonl, write_csv, write_jsonl
from .ex21 import edgar
from .ex21.normalize import entity_key, is_registrant, normalised_name, normalize_jurisdiction, sub_id
from .ex21.parse import assign_parents, parse_file

STEP = "subsidiaries"
PARSER_VERSION = "ex21_v2"

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


def _check(target: dict, status: str) -> dict:
    return {**target, "local_file": "", "rows_parsed": 0, "status": status}


def fetch(targets: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Manifest rows by accession (downloading what is not saved yet), and CHECK rows for the
    10-Ks with nothing to read. The manifest keeps 10-Ks no longer in filings.yaml; they are
    simply not used."""
    saved = {r["accession"]: r for r in read_csv(Paths.ex21_manifest())} \
        if Paths.ex21_manifest().exists() else {}
    checks = []
    for t in targets:
        row = saved.get(t["accession"])
        if row and (Paths.ex21_raw().parent / row["local_file"]).exists():
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


def parse(target: dict, entry: dict) -> tuple[list[dict], dict]:
    """Subsidiary rows of one 10-K, and its QA row."""
    parsed = parse_file(Paths.ex21_raw().parent / entry["local_file"])
    # An earlier manifest called the EDGAR name parent_name.
    registrant = entry.get("registrant_name") or entry.get("parent_name") or ""
    kept = [r for r in parsed if not is_registrant(r["subsidiary_name"], registrant)]
    rows = []
    for r in assign_parents(kept, target["ticker"]):
        country, region = normalize_jurisdiction(r["jurisdiction_raw"])
        rows.append({
            "sub_id": sub_id(target["cik"], r["subsidiary_name"]),
            "name": r["subsidiary_name"],
            "normalised_name": normalised_name(r["subsidiary_name"]),
            "entity_key": entity_key(r["subsidiary_name"]),
            "parent_cik": target["cik"], "parent_ticker": target["ticker"],
            "accession_number": target["accession"],
            "reporting_period_end": target["reporting_period_end"],
            "jurisdiction_raw": r["jurisdiction_raw"], "country": country, "state_or_region": region,
            "ownership_pct": r["ownership_pct"], "level": r["level"],
            "immediate_parent": r["immediate_parent"], "relationship": r["relationship"],
            "row_order": r["row_order"], "parse_method": r["parse_method"], "flags": r["flags"],
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


def catalog(rows: list[dict]) -> list[dict]:
    """Entity-catalog rows (entity_annotation.validate_catalog format), one per sub_id.

    A subsidiary applies to every filing of its parent in filings.yaml (the 10-Qs too: they
    carry no Exhibit 21 of their own). Everything is needs_review: nothing here is checked by
    a person yet, so step 7b would report a hit as candidate_match_needs_review, not matched.
    """
    accessions: dict[str, list[str]] = {}
    for f in filings():
        accessions.setdefault(str(f["cik"]).zfill(10), []).append(f["accession"])
    out: dict[str, dict] = {}
    for r in rows:
        entry = out.setdefault(r["sub_id"], {
            "entity_id": r["sub_id"], "entity_type": "subsidiary", "issuer_cik": r["parent_cik"],
            "name": r["name"], "aliases": [], "accessions": accessions.get(r["parent_cik"], []),
            "review_status": "needs_review", "source": "exhibit_21",
        })
        if r["name"] != entry["name"] and r["name"] not in entry["aliases"]:
            entry["aliases"].append(r["name"])
    return list(out.values())


def run() -> None:
    targets = ten_ks()
    if not targets:
        log(STEP, "no form: 10-K rows in filings.yaml; nothing to do")
        return
    saved, qa_rows = fetch(targets)
    skipped = {q["accession"] for q in qa_rows}
    rows = []
    for t in targets:
        if t["accession"] in skipped or t["accession"] not in saved:
            continue
        found, qa = parse(t, saved[t["accession"]])
        rows += found
        qa_rows.append(qa)
    write_jsonl(Paths.subsidiaries(), rows)
    write_csv(Paths.subsidiary_qa(), qa_rows, QA_COLS)
    entries = catalog(rows)
    Paths.subsidiary_catalog().write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    log(STEP, f"{len(targets)} 10-Ks -> {len(rows)} subsidiary rows, {len(entries)} distinct "
              f"subsidiaries -> {Paths.subsidiaries()}")
    for q in qa_rows:
        if q["status"] != "OK":
            log(STEP, f"CHECK {q['ticker']} {q['accession']}: {q['status']}")


def read_rows() -> list[dict]:
    return list(read_jsonl(Paths.subsidiaries())) if Paths.subsidiaries().exists() else []


if __name__ == "__main__":
    run()
