"""Schema probe for CW's proposed ontology — definition and loader.

One constructed 10-K/amendment plus six hand-selected Apple statements.
This is not an extraction pipeline or a jointly approved schema.
The passage is invented. It is shaped to exercise the parts of the schema that are
still open, which a clean filing would not.

Run this and you get an identical graph on any machine: every key is derived from
content, so MERGE matches instead of duplicating.
"""

import hashlib
import os
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
PROBE_ID = "cw-ontology-draft-20261016"

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "sketchpass")

CORPUS_VERSION = "schema-probe-v2"


# ------------------------------------------------------------------ keys

def key(prefix, *parts):
    """Deterministic key from content. The prefix only makes it readable."""
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def normalise(name):
    """Lowercase, drop the legal suffix, collapse whitespace."""
    out = name.lower()
    for suffix in (" llc", " inc.", " inc", " corp.", " corp", " corporation", ","):
        out = out.replace(suffix, "")
    return " ".join(out.split())


# ------------------------------------------------------------------ provenance
# Three shapes, because one contract does not cover all three sources.
# Neo4j has no nested maps, so these flatten to prov_* properties.

def from_text(chunk_id, start, end, section_id=None, accession=None):
    """Anything extracted from prose.

    Accession and section are supplied explicitly; chunk_id is opaque.
    Pass section_id when the passage sits in a section we model. Edge keys
    anchor on it, so the key survives a re-chunk. Keep the chunk id too - it
    is what cites an exact span.
    """
    return {
        "prov_shape": "text",
        "prov_source_chunk_id": chunk_id,
        "prov_section_id": section_id,
        "prov_accession": accession,
        "prov_char_start": start,
        "prov_char_end": end,
        "corpus_version": CORPUS_VERSION,
    }


def from_xbrl(accession, context_ref, unit_ref):
    """XBRL facts. No source_chunk_id — a fact is not in a text chunk."""
    return {
        "prov_shape": "xbrl",
        "prov_accession": accession,
        "prov_context_ref": context_ref,
        "prov_unit_ref": unit_ref,
        "corpus_version": CORPUS_VERSION,
    }


def from_metadata(source):
    """Anything derived from EDGAR metadata rather than a document."""
    return {
        "prov_shape": "metadata",
        "prov_derived_from": source,
        "corpus_version": CORPUS_VERSION,
    }


# ------------------------------------------------------------------ identifiers

NORTHWIND = "0000123456"
COASTAL = "0000987654"
TENK = "0000123456-25-000012"
TENKA = "0000123456-25-000031"

# Explicit local fixture section keys, not Data-issued production identifiers.
# They depend on filing/document role and section, not offsets or chunks.
SEC_ITEM7 = key("sec", TENK, "primary/item7")
SEC_EX21 = key("sec", TENK, "ex21/subsidiary-list")
SEC_RESTATEMENT = key("sec", TENKA, "primary/restatement")
SUB_HARBOR = key("sub", NORTHWIND, normalise("Harbor Logistics LLC"))
SEG_FREIGHT = key("seg", NORTHWIND, normalise("Freight Services"))
SEG_WAREHOUSE = key("seg", NORTHWIND, normalise("Warehousing Solutions"))

FACT_24_FILED = key("f", TENK, "Revenues", "c-freight-FY2024", "USD")
FACT_24_REST = key("f", TENKA, "Revenues", "c-freight-FY2024", "USD")
FACT_23_FILED = key("f", TENK, "Revenues", "c-freight-FY2023", "USD")


# ------------------------------------------------------------------ nodes

NODES = [
    (NORTHWIND, "Company", {
        "legal_name": "Northwind Industries, Inc.", "ticker": "NWI", "sic": "4731",
        "valid_from": "1998-06-01",
        **from_metadata("EDGAR submissions API"),
    }),
    (COASTAL, "Company", {
        "legal_name": "Coastal Freight Corp", "ticker": "CFRT", "sic": "4731",
        "valid_from": "2004-02-11",
        **from_metadata("EDGAR submissions API"),
    }),
    (TENK, "Filing", {
        "form_type": "10-K", "filed_date": "2025-02-18", "period_end": "2024-12-31",
        **from_metadata("EDGAR full-text index"),
    }),
    (TENKA, "Filing", {
        "form_type": "10-K/A", "filed_date": "2025-09-09", "period_end": "2024-12-31",
        "amends_accession": TENK,
        **from_metadata("EDGAR full-text index"),
    }),
    (SEC_ITEM7, "Section", {
        "item": "Item 7", "title": "Management's Discussion and Analysis",
        **from_text("fixture-c0412", 184_205, 191_880, section_id=SEC_ITEM7, accession=TENK),
    }),
    (SUB_HARBOR, "Subsidiary", {
        "name": "Harbor Logistics LLC", "jurisdiction": "Delaware",
        "ownership": "wholly owned",
        **from_text("fixture-ex21-c0003", 1_204, 1_262, section_id=SEC_EX21, accession=TENK),
    }),
    (SEG_FREIGHT, "Segment", {
        "name": "Freight Services", "reportable": True,
        **from_text("fixture-c0398", 176_430, 176_512, section_id=SEC_ITEM7, accession=TENK),
    }),
    (SEG_WAREHOUSE, "Segment", {
        "name": "Warehousing Solutions", "reportable": True,
        **from_text("fixture-c0398", 176_513, 176_598, section_id=SEC_ITEM7, accession=TENK),
    }),
    (FACT_24_FILED, "Fact", {
        "concept": "Revenues", "value": 412_000_000.0, "unit": "USD",
        "period_start": "2024-01-01", "period_end": "2024-12-31", "basis": "as_filed",
        **from_xbrl(TENK, "c-freight-FY2024", "USD"),
    }),
    (FACT_24_REST, "Fact", {
        "concept": "Revenues", "value": 388_400_000.0, "unit": "USD",
        "period_start": "2024-01-01", "period_end": "2024-12-31", "basis": "as_restated",
        **from_xbrl(TENKA, "c-freight-FY2024", "USD"),
    }),
    (FACT_23_FILED, "Fact", {
        "concept": "Revenues", "value": 286_500_000.0, "unit": "USD",
        "period_start": "2023-01-01", "period_end": "2023-12-31", "basis": "as_filed",
        **from_xbrl(TENK, "c-freight-FY2023", "USD"),
    }),
]


# ------------------------------------------------------------------ edges
# CONSOLIDATED_INTO is a CW proposal, pending review. Historical checks
# compare parent-only traversal with this explicit, evidence-backed edge.

EDGES = [
    (NORTHWIND, TENK, "FILES", from_metadata("EDGAR submissions API")),
    (NORTHWIND, TENKA, "FILES", from_metadata("EDGAR submissions API")),
    (TENKA, TENK, "AMENDS", from_metadata("EDGAR submissions API")),
    (TENK, SEC_ITEM7, "CONTAINS",
     from_text("fixture-c0412", 184_205, 191_880, section_id=SEC_ITEM7, accession=TENK)),
    (NORTHWIND, SUB_HARBOR, "OWNS", {
        "valid_from": "2024-04-03",
        "quote": "Harbor Logistics LLC — Delaware — wholly owned",
        **from_text("fixture-ex21-c0003", 1_204, 1_262, section_id=SEC_EX21, accession=TENK),
    }),
    (NORTHWIND, SEG_FREIGHT, "HAS_SEGMENT",
     from_text("fixture-c0398", 176_430, 176_512, section_id=SEC_ITEM7, accession=TENK)),
    (NORTHWIND, SEG_WAREHOUSE, "HAS_SEGMENT",
     from_text("fixture-c0398", 176_513, 176_598, section_id=SEC_ITEM7, accession=TENK)),
    (SEG_FREIGHT, FACT_24_FILED, "SCOPES", from_xbrl(TENK, "c-freight-FY2024", "USD")),
    (SEG_FREIGHT, FACT_24_REST, "SCOPES", from_xbrl(TENKA, "c-freight-FY2024", "USD")),
    (SEG_FREIGHT, FACT_23_FILED, "SCOPES", from_xbrl(TENK, "c-freight-FY2023", "USD")),
    (TENK, FACT_24_FILED, "REPORTS", from_xbrl(TENK, "c-freight-FY2024", "USD")),
    (TENK, FACT_23_FILED, "REPORTS", from_xbrl(TENK, "c-freight-FY2023", "USD")),
    (TENKA, FACT_24_REST, "REPORTS", from_xbrl(TENKA, "c-freight-FY2024", "USD")),
    (NORTHWIND, COASTAL, "PEER_OF", {
        "basis": "SIC 4731",
        **from_metadata("EDGAR SIC code, not a self-declared peer group"),
    }),
    (SUB_HARBOR, SEG_FREIGHT, "CONSOLIDATED_INTO", {
        "proposal_status": "CW draft; pending review",
        "valid_from": "2024-04-03",
        "quote": "Harbor's results are consolidated within the Freight Services "
                 "segment from the acquisition date.",
        **from_text("fixture-c0412", 184_612, 184_726, section_id=SEC_ITEM7, accession=TENK),
    }),
]

# Keep numeric fixture values outside Neo4j. This dictionary is a test-only
# stand-in for Structured's fact store, not a DuckDB loader or real XBRL data.
FACT_VALUES = {}
for nid, label, props in NODES:
    if label == "Fact":
        FACT_VALUES[nid] = props.pop("value")
        props.update(fact_id=nid, entity_cik=NORTHWIND,
                     dimensions=json.dumps({"segment": "Freight Services"}, sort_keys=True))
    elif label == "Company":
        props["cik"] = nid
    elif label == "Filing":
        props["accession_number"] = nid
        props["form"] = props.pop("form_type")
        props["filing_date"] = props.pop("filed_date")
    elif label == "Section":
        props.update(section_id=nid, section_key="primary/item7", is_constructed=True)
    elif label == "Subsidiary":
        props["sub_id"] = nid
    elif label == "Segment":
        props["segment_id"] = nid

# Supplied by the constructed amendment's evidence, NOT guessed from AMENDS.
FACT_REPLACEMENTS = {FACT_24_REST: FACT_24_FILED}

NODES.extend([
    (SEC_EX21, "Section", {"section_id": SEC_EX21, "section_key": "ex21/subsidiary-list",
      "item": "Exhibit 21", "title": "Subsidiaries", "is_constructed": True,
      **from_text("fixture-ex21", 1204, 1262, SEC_EX21, TENK)}),
    (SEC_RESTATEMENT, "Section", {"section_id": SEC_RESTATEMENT,
      "section_key": "primary/restatement", "item": "Restatement", "is_constructed": True,
      **from_text("fixture-amendment", 0, 100, SEC_RESTATEMENT, TENKA)}),
])
EDGES.extend([
    (TENK, SEC_EX21, "CONTAINS", from_text("fixture-ex21", 1204, 1262, SEC_EX21, TENK)),
    (TENKA, SEC_RESTATEMENT, "CONTAINS", from_text("fixture-amendment", 0, 100, SEC_RESTATEMENT, TENKA)),
])
# PEER_OF is still unresolved in the worksheet; leave it out of this probe.
EDGES[:] = [e for e in EDGES if e[2] != "PEER_OF"]
NODES[:] = [n for n in NODES if n[0] != COASTAL]

APPLE = "0000320193"
APPLE_TENK = "0000320193-25-000079"
APPLE_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
SEC_APPLE_1A = key("sec", APPLE_TENK, "primary/item1a")
NODES.extend([
    (APPLE, "Company", {"cik": APPLE, "ticker": "AAPL", "legal_name": "Apple Inc.",
      **from_metadata("Apple 2025 10-K cover")}),
    (APPLE_TENK, "Filing", {"accession_number": APPLE_TENK, "form": "10-K",
      "source_url": APPLE_URL, **from_metadata("Apple 2025 10-K")}),
    (SEC_APPLE_1A, "Section", {"section_id": SEC_APPLE_1A, "section_key": "primary/item1a",
      "item": "Item 1A", "title": "Risk Factors", "source_url": APPLE_URL,
      "locator_status": "Manual quote; Data key and XPath pending",
      **from_text(None, None, None, SEC_APPLE_1A, APPLE_TENK)}),
])
EDGES.extend([
    (APPLE, APPLE_TENK, "FILES", from_metadata("Apple 2025 10-K cover")),
    (APPLE_TENK, SEC_APPLE_1A, "CONTAINS", from_text(None, None, None, SEC_APPLE_1A, APPLE_TENK)),
])

# Candidate vocabulary for this probe only. Assignments remain needs_review.
THEMES = {"international_exposure": "International exposure", "supply_chain": "Supply chain", "competition": "Competition",
          "cybersecurity": "Cybersecurity", "business_interruption": "Business interruption"}
for rid, name in THEMES.items():
    NODES.append((key("risk", rid), "RiskFactor", {"risk_id": rid, "name": name,
      "extraction_status": "needs_review", **from_metadata("CW candidate theme vocabulary")}))


def parse_claim_examples(path):
    """Read six hand-selected annotations, not arbitrary filings."""
    blocks = re.split(r"^## (\d{2}) — [^\n]+\n", path.read_text(), flags=re.M)
    records = []
    for i in range(1, len(blocks), 2):
        number, body = blocks[i], blocks[i + 1]
        quote = re.search(r"\*\*Original sentence\*\*\s*\n> ([^\n]+)", body)
        label = re.search(r"\*\*(?:Primary label|Label):\*\* (\w+)", body)
        if not quote or not label:
            raise ValueError(f"Missing sentence/label in example {number}")
        context = re.search(r"\*\*Context — preceding sentence\*\*\s*\n> ([^\n]+)", body)
        notes = re.findall(r"\*\*(?:Mixed-content note|Context note|Interpretation limit):\*\* ([^\n]+)", body)
        records.append({"number": number, "quote": quote[1], "claim_type": label[1],
          "context": context[1] if context else "", "annotation_note": " ".join(notes)})
    if len(records) != 6:
        raise ValueError(f"Expected six examples, found {len(records)}")
    return records


CLAIM_RECORDS = parse_claim_examples(ROOT / "claim_examples.md")
CLAIM_KEYS = []
CLAIM_THEMES = {"01": "international_exposure", "02": "supply_chain", "03": "competition",
                "04": "cybersecurity", "05": "supply_chain", "06": "business_interruption"}
CLAIM_SCOPES = {"01": "Apple international sales", "02": "Apple logistics arrangements",
    "03": "Competition faced by Apple", "04": "Global cyberattack trends",
    "05": "Possible recurrence of semiconductor supply disruptions affecting Apple",
    "06": "Apple insurance coverage for business interruption losses"}
for record in CLAIM_RECORDS:
    # Deduplicate identical sentences within a section, independent of chunks.
    # Repeated occurrences will require stable occurrence locators at scale.
    cid = key("claim", APPLE_TENK, SEC_APPLE_1A, " ".join(record["quote"].split()))
    CLAIM_KEYS.append(cid)
    props = {k: v for k, v in record.items() if k != "number"}
    props.update(claim_id=cid, example_id=record["number"], section_id=SEC_APPLE_1A,
      accession_number=APPLE_TENK, source_url=APPLE_URL, extraction_status="needs_review",
      subject_scope=CLAIM_SCOPES[record["number"]],
      **from_text(None, None, None, SEC_APPLE_1A, APPLE_TENK))
    NODES.append((cid, "Claim", props))
    prov = {"quote": record["quote"], **from_text(None, None, None, SEC_APPLE_1A, APPLE_TENK)}
    EDGES.append((SEC_APPLE_1A, cid, "SUPPORTS", prov))
    EDGES.append((cid, key("risk", CLAIM_THEMES[record["number"]]), "CONCERNS_RISK",
      {**prov, "extraction_status": "needs_review", "annotation_note": "Manual candidate theme; pending review"}))

LABELS = sorted({label for _, label, _ in NODES})

def edge_key(src, rel, dst, props):
    """Anchor text on accession AND section; never parse or key on chunks."""
    if props["prov_shape"] == "text":
        anchor = (props.get("prov_accession"), props.get("prov_section_id"))
        if not all(anchor):
            raise ValueError("Text edge lacks accession or section")
    elif props["prov_shape"] == "xbrl":
        anchor = (props["prov_accession"], props["prov_context_ref"], props["prov_unit_ref"])
    else:
        anchor = (props["prov_derived_from"],)
    return key("e", src, rel, dst, *anchor)


def validate_fixture():
    keys = [n[0] for n in NODES]
    assert len(keys) == len(set(keys)), "Duplicate node key"
    assert set(LABELS) == {"Company", "Filing", "Section", "Segment", "Subsidiary", "Fact", "Claim", "RiskFactor"}
    seen = set()
    for src, dst, rel, props in EDGES:
        assert src in keys and dst in keys, "Missing edge endpoint"
        eid = edge_key(src, rel, dst, props)
        assert eid not in seen, "Duplicate edge key"
        seen.add(eid)
        if props["prov_shape"] == "text":
            changed = {**props, "prov_source_chunk_id": "new-opaque-chunk", "prov_char_start": 999999}
            assert edge_key(src, rel, dst, changed) == eid, "Chunk-dependent edge key"
    assert all("value" not in p for _, label, p in NODES if label == "Fact")
    assert len(CLAIM_KEYS) == 6
    assert {r["claim_type"] for r in CLAIM_RECORDS} == {"stated_fact", "forward_looking", "hedged_risk"}
    for replacement, original in FACT_REPLACEMENTS.items():
        assert replacement in FACT_VALUES and original in FACT_VALUES


# ------------------------------------------------------------------ loading

def driver():
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv(ROOT / ".env")
    return GraphDatabase.driver(os.environ.get("NEO4J_URI", URI), auth=(
        os.environ.get("NEO4J_USER", USER), os.environ.get("NEO4J_PASSWORD", PASSWORD)))


def create_constraints(session):
    for label in LABELS:
        session.run(f"CREATE CONSTRAINT cw_probe_{label.lower()}_identity IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE (n.probe_id, n.key) IS UNIQUE").consume()


def load(session, rechunk=False):
    for nid, label, original in NODES:
        props = {**original, "probe_id": PROBE_ID}
        if rechunk and props.get("prov_shape") == "text":
            props["prov_source_chunk_id"] = "new-opaque-chunk"
        props["display"] = (props.get("ticker") or props.get("item") or props.get("name")
            or props.get("example_id") or props.get("form") or props.get("concept"))
        session.run(f"MERGE (n:{label} {{key: $key, probe_id: $probe}}) SET n += $props",
                    key=nid, probe=PROBE_ID, props=props).consume()
    labels = {nid: label for nid, label, _ in NODES}
    for src, dst, rel, original in EDGES:
        props = {**original, "probe_id": PROBE_ID}
        if rechunk and props["prov_shape"] == "text":
            props["prov_source_chunk_id"] = "new-opaque-chunk"
        result = session.run(
            f"MATCH (a:{labels[src]} {{key: $src, probe_id: $probe}}) "
            f"MATCH (b:{labels[dst]} {{key: $dst, probe_id: $probe}}) "
            f"MERGE (a)-[r:{rel} {{key: $key}}]->(b) SET r += $props RETURN r.key AS key",
            src=src, dst=dst, probe=PROBE_ID,
            key=edge_key(src, rel, dst, props), props=props).single()
        if result is None:
            raise RuntimeError(f"Could not load {rel}: missing endpoint")


def counts(session):
    n = session.run("MATCH (n {probe_id:$probe}) RETURN count(n) AS c", probe=PROBE_ID).single()["c"]
    e = session.run("MATCH ()-[r {probe_id:$probe}]->() RETURN count(r) AS c", probe=PROBE_ID).single()["c"]
    return n, e


def main():
    parser = argparse.ArgumentParser(description="CW draft schema probe; not extraction")
    parser.add_argument("--check-only", action="store_true", help="Validate fixture without Neo4j")
    parser.add_argument("--reset", action="store_true", help="Delete only this probe's nodes before loading")
    args = parser.parse_args()
    validate_fixture()
    print(f"Fixture valid: {len(NODES)} nodes, {len(EDGES)} relationships; 8 node types")
    if args.check_only:
        return
    with driver() as drv, drv.session() as session:
        if args.reset:
            session.run("MATCH (n {probe_id:$probe}) DETACH DELETE n", probe=PROBE_ID).consume()
        create_constraints(session)
        load(session)
        first = counts(session)
        assert first == (len(NODES), len(EDGES)), f"Unexpected probe contents {first}; --reset removes this probe only"
        load(session)
        assert counts(session) == first, "Repeated load changed counts"
        print(f"Repeated load: {first} — idempotent")
        load(session, rechunk=True)
        assert counts(session) == first, "Re-chunk created duplicates"
        print(f"Simulated re-chunk: {first} — no duplicates")
        load(session)


if __name__ == "__main__":
    main()
