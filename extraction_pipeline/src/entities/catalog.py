"""Step 6b - Entity catalog: the subsidiaries and segments a Claim may link to (#91 P1).

Input : data/output/subsidiary_catalog.json   step 6a (Exhibit 21), already scoped per filing
        a fact store (.duckdb with financial_facts_v1), optional: the XBRL segment members.
            Path from --facts, PIPELINE_FACT_STORE, or settings.yaml entity_catalog.fact_store.
            Without one the catalog has no segments, and the log says so.
        config/entity_linking.yaml              the registrant's own names
Output: data/entity_catalog.json                read by steps 7 (recorded only), 8 and 9

Three kinds of entry (entity_annotation.validate_catalog format):
  subsidiary   copied from step 6a (source exhibit_21; prior_year_only_accessions kept)
  segment      one per (company, segment label) on us-gaap:StatementBusinessSegmentsAxis;
               accessions = the filings whose facts use the member, so a reorganised segment
               is only offered to the years that report it. Labels come from the member QName
               ("aapl:GreaterChinaSegmentMember" -> "Greater China Segment", key "greater
               china"). entity_id = uuid5 of "<cik>|<normalised label>" (schema-ids-operations.md:
               Segment = hash(cik + normalised_name)). source xbrl.
  registrant   the company itself, so a mention of "Medtronic plc" is recognised and never
               linked. Not a graph entity.

Everything from Exhibit 21 or XBRL is needs_review; step 9 turns a one-entry exact-key hit on
such an entry into matched_auto (the team's review policy, #91 P1.5).

No OpenAI calls. The catalog is not part of a label request hash, so rebuilding it never
makes step 7 pay again.

Usage:
  python run_pipeline.py --steps entity_catalog
  python -m src.entity_catalog [--facts path/to/facts.duckdb]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from collections import Counter
from pathlib import Path

from ..common import ROOT, Paths, filings, load_config, log, settings
from .annotation import entry_keys, match_key, validate_catalog
from .ex21.normalize import normalised_name

STEP = "entity_catalog"
SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
SEGMENT_NAMESPACE = uuid.UUID("5d1e9a37-6b0c-4f2e-8a41-3c7b9e2f6d10")
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[0-9]+|[a-z]+")    # same as structured/segments.py


def catalog_path() -> Path:
    return Paths.entity_catalog()


def segment_label(qname: str) -> str:
    """'aapl:GreaterChinaSegmentMember' -> 'Greater China Segment' (structured/segments.py)."""
    words = _WORD.findall(str(qname or "").split(":")[-1])
    if words and words[-1] == "Member":
        words = words[:-1]
    return " ".join(words)


def segment_id(cik: str, label: str) -> str:
    return str(uuid.uuid5(SEGMENT_NAMESPACE, f"{str(cik).zfill(10)}|{normalised_name(match_key(label))}"))


def registrant_entries() -> list[dict]:
    names = load_config("entity_linking").get("registrant_names") or {}
    by_ticker: dict[str, dict] = {}
    for f in filings():
        t = f["ticker"].upper()
        e = by_ticker.setdefault(t, {"cik": str(f["cik"]).zfill(10), "accessions": []})
        e["accessions"].append(f["accession"])
    out = []
    for t, e in by_ticker.items():
        listed = names.get(t) or []
        if not listed:
            log(STEP, f"{t}: no registrant_names in config/entity_linking.yaml")
            continue
        out.append({"entity_id": f"registrant-{e['cik']}", "entity_type": "registrant",
                    "issuer_cik": e["cik"], "name": listed[0], "aliases": listed[1:],
                    "accessions": e["accessions"], "review_status": "reviewed",
                    "source": "config/entity_linking.yaml", "ticker": t})
    return out


def fact_store_path(arg: str | None = None) -> Path | None:
    raw = arg or os.environ.get("PIPELINE_FACT_STORE") or (settings().get("entity_catalog") or {}).get("fact_store")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def segment_members(path: Path) -> list[tuple[str, str, str]]:
    """(cik, accession, member QName) for every segment member in the store."""
    import duckdb                           # only this step needs it, and only with a store
    conn = duckdb.connect(str(path), read_only=True)
    try:
        rows = conn.execute(f"""
            SELECT DISTINCT f.cik, f.accession_number, (d.value ->> 'member')
            FROM financial_facts_v1 f, json_each(f.dimensions) AS d
            -- Parentheses needed: on older DuckDB (seen in Colab; 1.3.2 fails, 2.0 works) ->> binds
            -- looser than =, so 'axis' = '...' is read as the JSON path and the query fails.
            WHERE (d.value ->> 'axis') = '{SEGMENT_AXIS}' AND (d.value ->> 'member') IS NOT NULL
        """).fetchall()
    finally:
        conn.close()
    return [(str(c).zfill(10), a, m) for c, a, m in rows]


def segment_entries(members: list[tuple[str, str, str]]) -> list[dict]:
    """One entry per (company, segment label); only filings in filings.yaml count, and members
    matching config/entity_linking.yaml segment_member_skip (totals, "all other") are left out."""
    wanted = {f["accession"] for f in filings()}
    skip = [re.compile(p) for p in load_config("entity_linking").get("segment_member_skip") or []]
    out: dict[tuple[str, str], dict] = {}
    for cik, accession, member in sorted(members):
        if accession not in wanted or any(p.search(member.split(":")[-1]) for p in skip):
            continue
        label = segment_label(member)
        key = match_key(label)
        if not key:
            continue
        e = out.setdefault((cik, key), {
            "entity_id": segment_id(cik, label), "entity_type": "segment", "issuer_cik": cik,
            "name": label, "aliases": [], "accessions": [], "members": [],
            "review_status": "needs_review", "source": "xbrl"})
        if member not in e["members"]:
            e["members"].append(member)
        short = re.sub(r"\s+Segments?$", "", label)
        for alias in (label, short):
            if alias != e["name"] and alias not in e["aliases"]:
                e["aliases"].append(alias)
        if accession not in e["accessions"]:
            e["accessions"].append(accession)
    return list(out.values())


def build(facts: str | None = None) -> list[dict]:
    subs = json.loads(Paths.subsidiary_catalog().read_text(encoding="utf-8")) \
        if Paths.subsidiary_catalog().exists() else []
    if not subs:
        log(STEP, f"no {Paths.subsidiary_catalog()}: run step 6a (subsidiaries) first; no subsidiaries")
    store = fact_store_path(facts)
    segments = []
    if store and store.exists():
        segments = segment_entries(segment_members(store))
    else:
        log(STEP, "no fact store (--facts / PIPELINE_FACT_STORE / settings entity_catalog.fact_store): "
                  "no segments in the catalog")
    registrants = registrant_entries()
    own = {(r["issuer_cik"], k) for r in registrants for k in entry_keys(r)}
    clash = [e for e in subs + segments if any((e["issuer_cik"], k) in own for k in entry_keys(e))]
    if clash:
        log(STEP, f"{len(clash)} entries share a key with the registrant and will never be linked: "
                  + "; ".join(sorted({e['name'] for e in clash}))[:300])
    catalog = subs + segments + registrants
    validate_catalog(catalog)
    return catalog


def run(facts: str | None = None) -> None:
    catalog = build(facts)
    catalog_path().write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    counts = Counter(e["entity_type"] for e in catalog)
    log(STEP, f"{counts['subsidiary']} subsidiaries, {counts['segment']} segments, "
              f"{counts['registrant']} registrants -> {catalog_path()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--facts", help="fact store (.duckdb) with financial_facts_v1, for segments")
    run(parser.parse_args().facts)
