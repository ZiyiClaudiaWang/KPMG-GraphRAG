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
import re
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

# Accepted and not applied, matching the structured arm. Both arms are offered
# the same keys by the contract, so refusing here and ignoring there meant one
# filter dict worked on one arm and raised on the other, and every caller had
# to learn which was which.
#
#   fiscal_year    the graph stores reporting_period_end, and a fiscal year
#                  cannot be read off it for every filer
#   fiscal_period  the graph stores form (10-K, 10-Q) but not the quarter
#
# Ignoring a filter silently is its own hazard: the caller gets rows that look
# filtered and are not. Filter on reporting_period_end or form instead.
_IGNORED_FILTERS = {"fiscal_year", "fiscal_period"}

# Why each of the rest is refused, so the caller gets a reason and not a shrug.
_UNSUPPORTED_FILTERS = {
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
        # We read both the old and new names for the fields the extraction
        # pipeline is renaming, so one of each pair is always absent. Neo4j
        # warns about every missing property on every query, which would bury
        # a real warning under three paragraphs of noise.
        driver = GraphDatabase.driver(
            uri, auth=auth, notifications_disabled_classifications=["UNRECOGNIZED"]
        )
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
        if key in _FILTER_TO_PROPERTY or key == "max_hops" or key in _IGNORED_FILTERS:
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
        if isinstance(value, (list, tuple, set)):
            # A list used to compare against a scalar property and match
            # nothing, so a cross-company question looked like no evidence
            # rather than a filter the arm did not apply.
            values = list(value)
            if not values:
                raise ValueError(
                    f"{key} is an empty list; give at least one value or drop the filter"
                )
            clauses.append(f"{alias}.{prop} IN ${key}")
            params[key] = values
        else:
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
       sup.role AS role, sup.model AS model,
       // The extraction pipeline is replacing sentence ids with unit ids, and
       // Cypher returns null for a property that is not there rather than
       // failing. Read both so the pointer back to the source survives the
       // change in either direction.
       sup.sentence_ids AS sentence_ids, sup.context_unit_ids AS context_unit_ids,
       sup.risk_lead_unit_id AS risk_lead_unit_id,
       c.risk_lead_sentence AS risk_lead_sentence, c.risk_summary AS risk_summary,
       c.multi_fragment AS multi_fragment,
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
            # Whichever of the two the loader wrote. Never both.
            "source_unit_ids": row.get("sentence_ids") or row.get("context_unit_ids") or [],
            "role": row["role"],
            "model": row["model"],
            # Only set on Item 1A claims, and only by the newer loader.
            "risk_lead_unit_id": row.get("risk_lead_unit_id"),
            "risk_lead_sentence": row.get("risk_lead_sentence"),
            "risk_summary": row.get("risk_summary"),
            "multi_fragment": row.get("multi_fragment"),
            "hops": TRAVERSAL_HOPS,
        },
    }



_OWNERSHIP_QUESTION_RE = re.compile(
    r"^\s*does\s+(.+?)\s+own\s+(.+?)\s*[?.]?\s*$",
    re.IGNORECASE,
)

_COMPANY_MATCH_CYPHER = """
MATCH (c:Company)
WHERE toLower(c.ticker) = toLower($owner)
   OR toLower(c.name) = toLower($owner)
   OR toLower(c.name) STARTS WITH toLower($owner) + ' '
RETURN DISTINCT c.cik AS cik
LIMIT 2
"""

_SUBSIDIARY_MATCH_CYPHER = """
MATCH (s:Subsidiary)
WHERE toLower(s.name) = toLower($subsidiary)
   OR toLower(s.normalised_name) = toLower($subsidiary)
RETURN DISTINCT s.sub_id AS sub_id
LIMIT 25
"""

_OWNS_ANY_CYPHER = """
MATCH (c:Company), (s:Subsidiary)
WHERE c.cik = $cik AND s.sub_id IN $sub_ids
OPTIONAL MATCH (c)-[r:OWNS]->(s)
RETURN count(r) > 0 AS owns
"""


def _parse_ownership_question(query: str) -> tuple[str, str] | None:
    """Return ``(owner, subsidiary)`` for the narrow M14 ``Does X own Y?`` form."""
    match = _OWNERSHIP_QUESTION_RE.match(query)
    if match is None:
        return None
    owner = match.group(1).strip(" \t\r\n?.")
    subsidiary = match.group(2).strip(" \t\r\n?.")
    if not owner or not subsidiary:
        return None
    return owner, subsidiary


def _company_ciks(session: Any, owner: str) -> list[str]:
    rows = session.run(_COMPANY_MATCH_CYPHER, owner=owner).data()
    return sorted({str(row["cik"]) for row in rows if row.get("cik")})


def _subsidiary_ids(session: Any, subsidiary: str) -> list[str]:
    rows = session.run(_SUBSIDIARY_MATCH_CYPHER, subsidiary=subsidiary).data()
    return sorted({str(row["sub_id"]) for row in rows if row.get("sub_id")})


def _owns_any(session: Any, cik: str, sub_ids: list[str]) -> bool:
    row = session.run(_OWNS_ANY_CYPHER, cik=cik, sub_ids=sub_ids).single()
    return bool(row and row["owns"])


def _ownership_guard(session: Any, owner: str, subsidiary: str) -> dict[str, Any] | None:
    """Return M14 abstention only when both sides resolve and the OWNS edge is absent.

    Unknown or non-unique companies, and unknown subsidiaries, fall through to the
    ordinary retriever rather than being mislabeled as fabricated relationships.
    """
    ciks = _company_ciks(session, owner)
    if len(ciks) != 1:
        return None
    sub_ids = _subsidiary_ids(session, subsidiary)
    if not sub_ids:
        return None
    if _owns_any(session, ciks[0], sub_ids):
        return None
    return {
        "status": "abstain",
        "reason": "fabricated_relationship",
        "results": [],
    }


def search(query: str, filters: dict | None = None, top_k: int = 10,
           trace_id: str | None = None) -> list[dict[str, Any]] | dict[str, Any]:
    """Find Claims matching the query. Matches `retrieve.Backend`.

    Call it through `retrieve.graph_search`, which owns input validation,
    trace propagation, ranking and the final conformance check. Calling it
    directly skips all of that.
    """
    filters = filters or {}
    _check_filters(filters)
    ownership = _parse_ownership_question(query)

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
            if ownership is not None:
                decision = _ownership_guard(session, *ownership)
                if decision is not None:
                    return decision

            rows = session.run(
                cypher, index=FULLTEXT_INDEX, lucene=lucene, limit=limit, **params
            ).data()
    except contract.RetrievalUnavailableError:
        raise
    except Exception as exc:
        raise contract.RetrievalUnavailableError(f"graph query failed: {exc}") from exc

    return [_to_evidence(row, trace_id) for row in rows[:top_k]]
