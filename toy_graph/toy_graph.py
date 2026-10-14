"""Toy graph for schema v1 — definition and loader.

One constructed 10-K plus its amendment, modelled with the six settled node types.
The passage is invented. It is shaped to exercise the parts of the schema that are
still open, which a clean filing would not.

Run this and you get an identical graph on any machine: every key is derived from
content, so MERGE matches instead of duplicating.
"""

import hashlib
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "sketchpass")

CORPUS_VERSION = "toy-v1"


# ------------------------------------------------------------------ keys

def key(prefix, *parts):
    """Deterministic key from content. The prefix only makes it readable."""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]
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

def from_text(chunk_id, start, end):
    """Anything extracted from prose."""
    return {
        "prov_shape": "text",
        "prov_source_chunk_id": chunk_id,
        "prov_accession": chunk_id.split("#")[0],
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

SEC_ITEM7 = key("sec", TENK, "Item 7", 184_205)
SUB_HARBOR = key("sub", NORTHWIND, normalise("Harbor Logistics LLC"))
SEG_FREIGHT = key("seg", NORTHWIND, normalise("Freight Services"))
SEG_WAREHOUSE = key("seg", NORTHWIND, normalise("Warehousing Solutions"))

FACT_24_FILED = key("f", TENK, "Revenues", "c-freight-FY2024", "USD")
FACT_24_REST = key("f", TENKA, "Revenues", "c-freight-FY2024", "USD")
FACT_23_FILED = key("f", TENK, "Revenues", "c-freight-FY2023", "USD")


# ------------------------------------------------------------------ nodes

NODES = [
    (NORTHWIND, "COMPANY", {
        "legal_name": "Northwind Industries, Inc.", "ticker": "NWI", "sic": "4731",
        "valid_from": "1998-06-01",
        **from_metadata("EDGAR submissions API"),
    }),
    (COASTAL, "COMPANY", {
        "legal_name": "Coastal Freight Corp", "ticker": "CFRT", "sic": "4731",
        "valid_from": "2004-02-11",
        **from_metadata("EDGAR submissions API"),
    }),
    (TENK, "FILING", {
        "form_type": "10-K", "filed_date": "2025-02-18", "period_end": "2024-12-31",
        **from_metadata("EDGAR full-text index"),
    }),
    (TENKA, "FILING", {
        "form_type": "10-K/A", "filed_date": "2025-09-09", "period_end": "2024-12-31",
        "amends_accession": TENK,
        **from_metadata("EDGAR full-text index"),
    }),
    (SEC_ITEM7, "SECTION", {
        "item": "Item 7", "title": "Management's Discussion and Analysis",
        **from_text(f"{TENK}#c0412", 184_205, 191_880),
    }),
    (SUB_HARBOR, "SUBSIDIARY", {
        "name": "Harbor Logistics LLC", "jurisdiction": "Delaware",
        "ownership": "wholly owned",
        **from_text(f"{TENK}#ex21-c0003", 1_204, 1_262),
    }),
    (SEG_FREIGHT, "SEGMENT", {
        "name": "Freight Services", "reportable": True,
        **from_text(f"{TENK}#c0398", 176_430, 176_512),
    }),
    (SEG_WAREHOUSE, "SEGMENT", {
        "name": "Warehousing Solutions", "reportable": True,
        **from_text(f"{TENK}#c0398", 176_513, 176_598),
    }),
    (FACT_24_FILED, "FACT", {
        "concept": "Revenues", "value": 412_000_000.0, "unit": "USD",
        "period_start": "2024-01-01", "period_end": "2024-12-31", "basis": "as-filed",
        **from_xbrl(TENK, "c-freight-FY2024", "USD"),
    }),
    (FACT_24_REST, "FACT", {
        "concept": "Revenues", "value": 388_400_000.0, "unit": "USD",
        "period_start": "2024-01-01", "period_end": "2024-12-31", "basis": "restated",
        **from_xbrl(TENKA, "c-freight-FY2024", "USD"),
    }),
    (FACT_23_FILED, "FACT", {
        "concept": "Revenues", "value": 286_500_000.0, "unit": "USD",
        "period_start": "2023-01-01", "period_end": "2023-12-31", "basis": "as-filed",
        **from_xbrl(TENK, "c-freight-FY2023", "USD"),
    }),
]


# ------------------------------------------------------------------ edges
# CONSOLIDATED_INTO is NOT in settled schema v1. It is loaded here flagged
# in_schema_v1 = false, because the toy question cannot be answered without it.
# See README, finding one.

EDGES = [
    (NORTHWIND, TENK, "FILES", from_metadata("EDGAR submissions API")),
    (NORTHWIND, TENKA, "FILES", from_metadata("EDGAR submissions API")),
    (TENKA, TENK, "AMENDS", from_metadata("EDGAR submissions API")),
    (TENK, SEC_ITEM7, "CONTAINS", from_text(f"{TENK}#c0412", 184_205, 191_880)),
    (NORTHWIND, SUB_HARBOR, "OWNS", {
        "valid_from": "2024-04-03",
        "quote": "Harbor Logistics LLC — Delaware — wholly owned",
        **from_text(f"{TENK}#ex21-c0003", 1_204, 1_262),
    }),
    (NORTHWIND, SEG_FREIGHT, "REPORTS_SEGMENT",
     from_text(f"{TENK}#c0398", 176_430, 176_512)),
    (NORTHWIND, SEG_WAREHOUSE, "REPORTS_SEGMENT",
     from_text(f"{TENK}#c0398", 176_513, 176_598)),
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
        "in_schema_v1": False,
        "valid_from": "2024-04-03",
        "quote": "Harbor's results are consolidated within the Freight Services "
                 "segment from the acquisition date.",
        **from_text(f"{TENK}#c0412", 184_612, 184_726),
    }),
]

LABELS = sorted({label for _, label, _ in NODES})


def edge_key(src, rel, dst, props):
    """Edge keys include the chunk they came from, so two sources give two edges."""
    return key("e", src, rel, dst, props.get("prov_source_chunk_id", "-"))


# ------------------------------------------------------------------ loading

def driver():
    return GraphDatabase.driver(URI, auth=(USER, PASSWORD))


def create_constraints(session):
    """One uniqueness constraint per label, on the deterministic key."""
    for label in LABELS:
        session.run(
            f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.key IS UNIQUE"
        )


def load(session):
    """All nodes first, then all edges.

    Order is not optional. A MATCH that finds nothing fails silently, so an edge
    whose endpoints are missing is simply never created and nothing reports it.
    """
    for node_key, label, props in NODES:
        session.run(
            f"MERGE (n:{label} {{key: $key}}) SET n += $props",
            key=node_key, props=props,
        )
    for src, dst, rel, props in EDGES:
        session.run(
            f"MATCH (a {{key: $src}}) MATCH (b {{key: $dst}}) "
            f"MERGE (a)-[r:{rel} {{key: $key}}]->(b) SET r += $props",
            src=src, dst=dst, key=edge_key(src, rel, dst, props), props=props,
        )


def set_captions(session):
    """Readable captions for Neo4j Browser. Display only, nothing reads these."""
    captions = [
        ("COMPANY", "n.ticker"),
        ("FILING", "n.form_type"),
        ("SECTION", "n.item"),
        ("SUBSIDIARY", "n.name"),
        ("SEGMENT", "n.name"),
        ("FACT", "n.concept + ' ' + left(n.period_end, 4) + ' $' + "
                 "toString(round(n.value / 1000000.0, 1)) + 'm (' + n.basis + ')'"),
    ]
    for label, expr in captions:
        session.run(f"MATCH (n:{label}) SET n.display = {expr}")


def counts(session):
    n = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    e = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    return n, e


def main():
    print(f"connecting to {URI}")
    with driver() as drv, drv.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
        create_constraints(session)

        load(session)
        first = counts(session)
        print(f"after first load:  {first[0]} nodes, {first[1]} relationships")

        load(session)
        second = counts(session)
        print(f"after second load: {second[0]} nodes, {second[1]} relationships")
        print("idempotent" if first == second
              else "NOT IDEMPOTENT — a key is not deterministic")

        set_captions(session)

        print("\nnodes by label")
        for row in session.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY label"
        ):
            print(f"  {row['label']:<11} {row['c']}")
        print("relationships by type")
        for row in session.run(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS c ORDER BY rel"
        ):
            print(f"  {row['rel']:<18} {row['c']}")


if __name__ == "__main__":
    main()
