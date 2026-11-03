"""The graph arm: search Claims in Neo4j and return contract-shaped evidence.

This is the backend that `retrieve.configure_graph_backend()` has been waiting
for. It reads the graph the extraction pipeline writes:

    (Company)-[:FILES]->(Filing)-[:CONTAINS]->(Section)-[:SUPPORTS]->(Claim)

A Claim holds the sentence and its type. Everything else the evidence envelope
needs — company, form, accession, filing date — lives further up that path, so
every search walks all four nodes. The envelope is why the traversal is three
hops and not one.

Claims cite sections, so `section_id` is the provenance key and `chunk_id` is
always null. That is the contract's rule: exactly one of `chunk_id`,
`section_id` or `metadata.fact_id` identifies any evidence object.

Connect with NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD, then register it:

    from graphrag import graph_backend, retrieve
    retrieve.configure_graph_backend(graph_backend.search)
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import contract

# Lexical search over the claim text. Neo4j scores the match for us, which is
# what the envelope's `score` reports.
FULLTEXT_INDEX = "claim_quote_fulltext"

EXTRACTION_VERSION = "claim-pipeline-v1"
INDEX_VERSION = "neo4j-fulltext-v1"

# Filters that map onto a property this graph actually stores. Anything in
# the contract's graph filter set but missing here is refused by name rather
# than ignored, because an ignored filter silently widens the search.
_FILTER_TO_PROPERTY = {
    "ticker": ("co", "ticker"),
    "cik": ("co", "cik"),
    "form": ("fl", "form"),
    "accession_number": ("fl", "accession_number"),
    "filing_date": ("fl", "filing_date"),
    "reporting_period_end": ("fl", "reporting_period_end"),
    "section": ("sec", "section_key"),
}

# Why each of the rest is refused, so the caller gets a reason and not a shrug.
_UNSUPPORTED_FILTERS = {
    "fiscal_year": (
        "the graph stores reporting_period_end, and a fiscal year cannot be read "
        "off it for every filer; filter on reporting_period_end instead"
    ),
    "fiscal_period": (
        "the graph stores form (10-K, 10-Q) but not the quarter; filter on form "
        "or reporting_period_end instead"
    ),
    "is_table": (
        "Claims are extracted from prose only, so every result would match "
        "is_table=False and nothing would match is_table=True"
    ),
}

# Claim -> Section -> Filing -> Company. A caller asking for fewer than this
# is asking for an envelope the graph cannot fill.
TRAVERSAL_HOPS = 3

_driver = None
_database: str | None = None

# Defined in the contract so retrieve.py can let it through without importing
# a backend.
FilterNotSupportedError = contract.FilterNotSupportedError


def connect(uri: str | None = None, auth: tuple[str, str] | None = None,
            database: str | None = None):
    """Open the driver, or reuse the one already open.

    Credentials come from the environment unless passed in. Raises
    `RetrievalUnavailableError` if the database cannot be reached, so a dead
    store looks the same here as it does everywhere else in the contract.
    """
    global _driver, _database
    if _driver is not None and uri is None:
        return _driver

    from neo4j import GraphDatabase  # imported here so the rest of the module loads without it

    uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    if auth is None:
        auth = (os.environ.get("NEO4J_USER") or os.environ.get("NEO4J_USERNAME") or "neo4j",
                os.environ.get("NEO4J_PASSWORD", ""))
    _database = database or os.environ.get("NEO4J_DATABASE") or None

    try:
        driver = GraphDatabase.driver(uri, auth=auth)
        driver.verify_connectivity()
    except Exception as exc:
        raise contract.RetrievalUnavailableError(
            f"cannot reach the graph at {uri}: {exc}"
        ) from exc

    _driver = driver
    return _driver


def close() -> None:
    """Drop the driver. The next search opens a new one."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def ensure_index(driver=None) -> None:
    """Create the full-text index on Claim.quote if it is not there yet.

    Safe to call on every run. Without it the search returns nothing at all,
    which would read as 'no evidence' rather than 'not indexed'.
    """
    driver = driver or connect()
    with driver.session(database=_database) as session:
        session.run(
            f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
            "FOR (c:Claim) ON EACH [c.quote]"
        )
        session.run("CALL db.awaitIndexes(30000)")


def _check_filters(filters: dict) -> None:
    """Refuse a filter this graph cannot serve, by name and with a reason."""
    for key in filters:
        if key in _FILTER_TO_PROPERTY or key == "max_hops":
            continue
        reason = _UNSUPPORTED_FILTERS.get(key)
        if reason:
            raise FilterNotSupportedError(f"the graph arm cannot filter on {key}: {reason}")
        raise FilterNotSupportedError(f"the graph arm cannot filter on {key}")

    max_hops = filters.get("max_hops")
    if max_hops is not None and max_hops < TRAVERSAL_HOPS:
        raise FilterNotSupportedError(
            f"max_hops={max_hops} is below the {TRAVERSAL_HOPS} hops from Claim to Company "
            "that the evidence envelope needs"
        )


def _where_clause(filters: dict) -> tuple[str, dict]:
    """Turn the supported filters into Cypher and its parameters."""
    clauses, params = [], {}
    for key, value in filters.items():
        if key not in _FILTER_TO_PROPERTY:
            continue
        alias, prop = _FILTER_TO_PROPERTY[key]
        clauses.append(f"{alias}.{prop} = ${key}")
        params[key] = value
    return (" AND ".join(clauses), params)


# Lucene treats these as syntax. A question typed by a person is not Lucene,
# so they are stripped rather than escaped.
_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def _lucene_safe(query: str) -> str:
    cleaned = "".join(" " if ch in _LUCENE_SPECIAL else ch for ch in query)
    terms = [t for t in cleaned.split() if t]
    return " OR ".join(terms)


def source_url(cik: str, accession_number: str) -> str:
    """The EDGAR folder for a filing. The envelope requires a non-null URL and
    the graph does not store one, so it is built from keys the graph does hold."""
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession_number.replace('-', '')}/"
    )


SEARCH_CYPHER = """
CALL db.index.fulltext.queryNodes($index, $lucene, {limit: $limit})
YIELD node AS c, score
MATCH (co:Company)-[:FILES]->(fl:Filing)-[:CONTAINS]->(sec:Section)-[sup:SUPPORTS]->(c)
{where}
RETURN c.claim_id AS claim_id, c.quote AS quote, c.claim_type AS claim_type,
       c.subject_scope AS subject_scope, c.extraction_status AS extraction_status,
       c.section_id AS section_id, score,
       sec.section_key AS section_key, sec.title AS section_title,
       sec.section_type AS section_type, sec.xpath AS section_xpath,
       sup.raw_spans_json AS raw_spans_json, sup.section_path AS section_path,
       sup.sentence_ids AS sentence_ids, sup.role AS role, sup.model AS model,
       fl.accession_number AS accession_number, fl.form AS form,
       fl.filing_date AS filing_date, fl.reporting_period_end AS reporting_period_end,
       co.cik AS cik, co.name AS company_name, co.ticker AS ticker
ORDER BY score DESC
"""


def _parse_spans(raw: object) -> list:
    """Neo4j cannot store a nested list, so the loader writes spans as JSON text.

    Hand the caller a list, so they are not left parsing a string that only
    this module knows the shape of.
    """
    if not isinstance(raw, str):
        return raw or []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _to_evidence(row: dict, trace_id: str | None) -> dict[str, Any]:
    """Map one Cypher row onto the evidence envelope."""
    cik = str(row["cik"]).zfill(10)
    return {
        # The wrapper in retrieve.py overwrites rank after it sorts.
        "rank": 0,
        "chunk_id": None,
        "section_id": row["section_id"],
        "text": row["quote"],
        "quote": row["quote"],
        "score": float(row["score"]),
        "section": row["section_key"],
        "ticker": row["ticker"],
        "cik": cik,
        "company_name": row["company_name"],
        "form": row["form"],
        "accession_number": row["accession_number"],
        "filing_date": row["filing_date"],
        "reporting_period_end": row["reporting_period_end"],
        "source_url": source_url(cik, row["accession_number"]),
        "chunking_version": None,
        "extraction_version": EXTRACTION_VERSION,
        "index_version": INDEX_VERSION,
        "metadata": {
            "trace_id": trace_id,
            "retrieval_method": "neo4j_fulltext_claim",
            "claim_id": row["claim_id"],
            # What the statement is, and who it is about. Without scope an
            # agent reads a claim about a competitor as one about the filer.
            "claim_type": row["claim_type"],
            "subject_scope": row["subject_scope"],
            "extraction_status": row["extraction_status"],
            "section_title": row["section_title"],
            "section_type": row["section_type"],
            "section_path": row["section_path"],
            "xpath": row["section_xpath"],
            # Offsets are section-relative and counted in Unicode codepoints.
            "raw_spans": _parse_spans(row["raw_spans_json"]),
            "coordinate_system": "exact_data_section_content_unicode_codepoints",
            "sentence_ids": row["sentence_ids"],
            "role": row["role"],
            "model": row["model"],
            "hops": TRAVERSAL_HOPS,
        },
    }


def search(query: str, filters: dict | None = None, top_k: int = 10,
           trace_id: str | None = None) -> list[dict[str, Any]]:
    """Find Claims matching the query. Matches `retrieve.Backend`.

    Call it through `retrieve.graph_search`, which owns input validation,
    trace propagation, ranking and the final conformance check. Calling it
    directly skips all of that.
    """
    filters = filters or {}
    _check_filters(filters)

    lucene = _lucene_safe(query)
    if not lucene:
        return []

    where, params = _where_clause(filters)
    # replace, not format: the Cypher contains a map literal, {limit: $limit}.
    cypher = SEARCH_CYPHER.replace("{where}", f"WHERE {where}" if where else "")

    driver = connect()
    # Ask for more than top_k because the WHERE runs after the index, so
    # filtered-out claims would otherwise eat the result slots.
    limit = top_k * 10 if where else top_k

    try:
        with driver.session(database=_database) as session:
            rows = session.run(
                cypher, index=FULLTEXT_INDEX, lucene=lucene, limit=limit, **params
            ).data()
    except contract.RetrievalUnavailableError:
        raise
    except Exception as exc:
        raise contract.RetrievalUnavailableError(f"graph query failed: {exc}") from exc

    return [_to_evidence(row, trace_id) for row in rows[:top_k]]
