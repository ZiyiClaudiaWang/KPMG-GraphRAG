"""Subsidiaries - Exhibit 21 parent-subsidiary lists of the 10-Ks in config/filings.yaml.

Input : the form: 10-K rows of config/filings.yaml (ticker, cik, accession, period_end);
        10-Q rows are skipped, since only a 10-K carries Exhibit 21.
        Each 10-K's Exhibit 21 is downloaded from SEC EDGAR once and kept in
        data/ex21/raw/; a later run reads the saved copy and needs no network.
Output: data/output/subsidiaries.jsonl    one row per subsidiary per 10-K (loaded by step 8)
        data/output/subsidiary_qa.csv     one row per 10-K: rows parsed, and what to check
        data/output/subsidiary_catalog.json   the same subsidiaries in the entity-catalog format
                                          of entity_annotation.py, for linking Claims later.
                                          Not read by any step yet: data/entity_catalog.json is.

This step does not use the Data team's export or the OpenAI API, and nothing in it changes a
label request hash. Downloading needs SEC_USER_AGENT="Name email" (SEC requires a contact),
in the environment or in extraction_pipeline/.env.

Keep config/filings.yaml current: this step fetches exactly the 10-K accessions listed there,
and its outputs cover only those, so a 10-K removed from the list drops out on the next run.

Each subsidiary gets sub_id = uuid5(parent CIK | normalised name), the scheme agreed in
docs/schema-ids-operations.md: the same name under the same parent is one Subsidiary node
across filings. The key is a provisional identity, not proof that two names are one entity:
a renamed subsidiary gets a new sub_id.

A 10-K that cannot be downloaded (no SEC_USER_AGENT, no network) is listed in
subsidiary_qa.csv as CHECK and skipped, so a full pipeline run never stops here.

Usage:
  python run_pipeline.py --steps subsidiaries
  python -m src.subsidiaries
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

from .common import (data_dir, filings, load_env, log, read_csv, read_jsonl, write_csv,
                     write_jsonl)
from .ex21.normalize import normalize_jurisdiction
from .ex21.parse import assign_parents, parse_file

STEP = "subsidiaries"
PARSER_VERSION = "ex21_v1"

# Fixed namespace, so the same parent and name always give the same sub_id.
SUB_NAMESPACE = uuid.UUID("8b2f4c6e-1d3a-4e5b-9c7d-0a1f2e3d4c5b")

MANIFEST_COLS = ["ticker", "cik", "parent_name", "accession", "period_end", "fiscal_year",
                 "source_url", "local_file"]
QA_COLS = ["ticker", "fiscal_year", "accession", "local_file", "rows_parsed", "duplicate_names",
           "missing_jurisdiction", "multi_level", "parse_method", "status"]


class Paths:
    raw = staticmethod(lambda: data_dir() / "ex21" / "raw")
    manifest = staticmethod(lambda: data_dir() / "ex21" / "manifest.csv")
    rows = staticmethod(lambda: data_dir() / "output" / "subsidiaries.jsonl")
    qa = staticmethod(lambda: data_dir() / "output" / "subsidiary_qa.csv")
    catalog = staticmethod(lambda: data_dir() / "output" / "subsidiary_catalog.json")


# ---------- identity ----------

def normalised_name(name: str) -> str:
    """Case, accents and punctuation removed; legal forms kept ("Apple Asia LLC" and
    "Apple Asia Limited" are two companies)."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", n.replace("&", " and ")).strip()


def sub_id(parent_cik: str, name: str) -> str:
    return str(uuid.uuid5(SUB_NAMESPACE, f"{parent_cik}|{normalised_name(name)}"))


# ---------- SEC EDGAR ----------

def get(url: str) -> bytes:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        sys.exit("Set SEC_USER_AGENT='Name email' (SEC requires a contact), "
                 "in the environment or in extraction_pipeline/.env")
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(0.15)           # SEC allows 10 requests a second
                return r.read()
        except Exception:                  # 429 / transient network
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(url)


class _IndexRows(HTMLParser):
    """(cells, link) rows of an EDGAR '-index.html' page."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._href, self._in_td = [], [], "", None, False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row, self._href = [], None
        elif tag == "td":
            self._in_td, self._cell = True, ""
        elif tag == "a" and self._in_td:
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "td":
            self._row.append(self._cell.strip())
            self._in_td = False
        elif tag == "tr" and self._row:
            self.rows.append((self._row, self._href))

    def handle_data(self, data):
        if self._in_td:
            self._cell += data


def ex21_url(cik: str, accession: str) -> str | None:
    """Link to the filing's EX-21 / EX-21.1 document, or None when it has none."""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    p = _IndexRows()
    p.feed(get(base + f"{accession}-index.html").decode("utf-8", "replace"))
    for cells, href in p.rows:
        if href and any(re.fullmatch(r"EX-21(\.\d+)?", c.strip(), re.I) for c in cells):
            href = href.split("/ix?doc=")[-1]
            return "https://www.sec.gov" + href if href.startswith("/") else base + href
    return None


def company_name(cik: str) -> str:
    return json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json")).get("name") or ""


# ---------- step ----------

def ten_ks() -> list[dict]:
    rows = []
    for f in filings():
        if str(f.get("form", "")).upper() != "10-K":
            continue
        period = str(f.get("period_end", ""))
        rows.append({"ticker": f["ticker"].upper(), "cik": str(f["cik"]).zfill(10),
                     "accession": f["accession"], "period_end": period, "fiscal_year": period[:4]})
    return rows


def fetch(targets: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Manifest rows for the targets, downloading what is not saved yet.

    Returns (manifest rows by accession, QA rows for 10-Ks with no Exhibit 21 to read).
    The manifest keeps rows for 10-Ks no longer in filings.yaml; they are simply not used.
    """
    saved = {r["accession"]: r for r in read_csv(Paths.manifest())} if Paths.manifest().exists() else {}
    missing = []
    for t in targets:
        row = saved.get(t["accession"])
        if row and (Paths.raw().parent / row["local_file"]).exists():
            continue
        load_env()
        if not os.environ.get("SEC_USER_AGENT"):
            missing.append({**t, "local_file": "", "rows_parsed": 0,
                            "status": "CHECK: not downloaded; set SEC_USER_AGENT='Name email'"})
            log(STEP, f"{t['ticker']} {t['accession']}: not saved yet and SEC_USER_AGENT is not set; skipped")
            continue
        try:
            url = ex21_url(t["cik"], t["accession"])
            content = get(url) if url else b""
            parent_name = company_name(t["cik"]) if url else ""
        except Exception as e:             # offline, or SEC refused: the rest of the run goes on
            missing.append({**t, "local_file": "", "rows_parsed": 0,
                            "status": f"CHECK: download failed ({type(e).__name__}: {e})"})
            log(STEP, f"{t['ticker']} {t['accession']}: download failed: {e}")
            continue
        if not url:
            missing.append({**t, "local_file": "", "rows_parsed": 0,
                            "status": "CHECK: no EX-21 attached to this 10-K (incorporated by reference?)"})
            log(STEP, f"{t['ticker']} {t['accession']}: no EX-21 attached")
            continue
        local = f"raw/{t['ticker']}_FY{t['fiscal_year']}_{t['accession']}_ex21{Path(url).suffix or '.htm'}"
        Paths.raw().mkdir(parents=True, exist_ok=True)
        (Paths.raw().parent / local).write_bytes(content)
        saved[t["accession"]] = {**t, "parent_name": parent_name or t["ticker"],
                                 "source_url": url, "local_file": local}
        log(STEP, f"{t['ticker']} {t['accession']}: downloaded {local}")
    write_csv(Paths.manifest(), list(saved.values()), MANIFEST_COLS)
    return saved, missing


def parse(target: dict, entry: dict) -> tuple[list[dict], dict]:
    """Subsidiary rows of one 10-K, and its QA row."""
    path = Paths.raw().parent / entry["local_file"]
    parsed = assign_parents(parse_file(path), entry["parent_name"])
    rows = []
    for r in parsed:
        country, region = normalize_jurisdiction(r["jurisdiction_raw"])
        rows.append({
            "sub_id": sub_id(target["cik"], r["subsidiary_name"]),
            "name": r["subsidiary_name"],
            "normalised_name": normalised_name(r["subsidiary_name"]),
            "parent_cik": target["cik"], "parent_ticker": target["ticker"],
            "parent_name": entry["parent_name"],
            "accession_number": target["accession"], "fiscal_year": target["fiscal_year"],
            "period_end": target["period_end"],
            "jurisdiction_raw": r["jurisdiction_raw"], "country": country, "state_or_region": region,
            "ownership_pct": r["ownership_pct"], "level": r["level"],
            "immediate_parent": r["immediate_parent"], "relationship": r["relationship"],
            "row_order": r["row_order"], "parse_method": r["parse_method"], "flags": r["flags"],
            "source_url": entry["source_url"], "source_file": entry["local_file"],
            "parser_version": PARSER_VERSION,
        })
    names = [r["normalised_name"] for r in rows]
    qa = {**target, "local_file": entry["local_file"], "rows_parsed": len(rows),
          "duplicate_names": len(names) - len(set(names)),
          "missing_jurisdiction": sum(1 for r in rows if not r["jurisdiction_raw"]),
          "multi_level": int(any(r["level"] > 0 for r in rows)),
          "parse_method": ",".join(sorted({r["parse_method"] for r in rows})) or "NONE",
          "status": "OK" if rows else "CHECK: no rows parsed (image/PDF exhibit or unusual layout?)"}
    return rows, qa


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
    rows = []
    skipped = {q["accession"] for q in qa_rows}       # not downloaded: already a CHECK row
    for t in targets:
        if t["accession"] in skipped or t["accession"] not in saved:
            continue
        found, qa = parse(t, saved[t["accession"]])
        rows += found
        qa_rows.append(qa)
    write_jsonl(Paths.rows(), rows)
    write_csv(Paths.qa(), qa_rows, QA_COLS)
    entries = catalog(rows)
    Paths.catalog().write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    checks = [q for q in qa_rows if q["status"] != "OK"]
    log(STEP, f"{len(targets)} 10-Ks -> {len(rows)} subsidiary rows, {len(entries)} distinct subsidiaries "
              f"-> {Paths.rows()}")
    for q in checks:
        log(STEP, f"CHECK {q['ticker']} {q['accession']}: {q['status']}")


def read_rows() -> list[dict]:
    return list(read_jsonl(Paths.rows())) if Paths.rows().exists() else []


if __name__ == "__main__":
    run()
