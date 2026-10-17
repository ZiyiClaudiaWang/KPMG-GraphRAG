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
