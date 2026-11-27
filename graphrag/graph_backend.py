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

import datetime
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import contract

# The fiscal calendar lives with the structured arm, which imports by bare
# name, so its directory goes on the path the way backends.py does it. A
# pyproject.toml would make this an ordinary import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "structured"))
import fiscal  # noqa: E402

# Nothing in this package logs yet. A library should not configure logging, and
# it does not need to here: Python prints WARNING and above to stderr through
# its last-resort handler even when the application sets nothing up. So the one
# warning below is visible by default and still silenceable by a caller.
log = logging.getLogger(__name__)

# Lexical search over the claim text. Neo4j scores the match for us, which is
# what the envelope's `score` reports.
FULLTEXT_INDEX = "claim_quote_fulltext"

EXTRACTION_VERSION = "claim-pipeline-v1"
INDEX_VERSION = "neo4j-fulltext-v1"

# How many claims to ask the index for when a filter is present.
#
# The filter runs after the index, not inside it. So the index is asked for the
# best N claims in the whole graph, and only then are the ones outside the
# filter thrown away. When the filter selects a small share of the corpus, the
# rows it wants can sit far down the global score order and never reach the
# WHERE at all: measured on 10,774 claims, "revenue" filtered to one quarter of
# one company returned nothing at top_k=8, because its four claims sat at
# positions 227, 461, 488 and 498. The old rule asked for top_k * 10, which at
# top_k=8 is 80.
#
# So a multiple of top_k cannot be right - top_k says how many rows the caller
# wants back, and nothing about how far down the list they start. The rule is
# instead: ask the index for every claim matching the query, and let the filter
# choose from all of them. That is correct by construction rather than by a
# large enough guess, and it costs one extra round trip to learn the size of
# the match set.
#
# The match set is bounded by the query, not the corpus - but not by much. The
# query is built by joining every word with OR, and the index analyzer keeps
# stop words, so a question matches most of the graph. Measured on 10,774
# claims: "revenue" alone matches 548 (5%), but "total revenue for the three
# months ended March 31, 2026" matches 7,485 (70%) and the demo question
# matches 8,305 (77%).
#
# So the cap has to clear the whole corpus, not a fraction of it. The 313-filing
# corpus is around 168,000 claims, and 25,000 - the first value here - would
# have fired on most questions there, silently returning this defect. A cap that
# fires is this bug again, so the cap is set above the corpus we are loading and
# the code says so when it is reached.
#
# It stays a cap rather than becoming unbounded because a backstop that can
# never trigger is not a backstop. Cost at this size is unmeasured: 25,000
# candidates took 0.18s on 10,774 claims, and nothing has been run against a
# corpus large enough to need more. Measure after the v2 load.
FULLTEXT_MATCH_CAP = 250_000

COUNT_MATCHES_CYPHER = """
CALL db.index.fulltext.queryNodes($index, $lucene, {limit: $limit})
YIELD node
RETURN count(node) AS matches
"""

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

# Nothing is accepted and dropped any more. fiscal_year and fiscal_period used
# to sit here, on the grounds that a fiscal year cannot be read off a
# reporting_period_end for every filer. It can: each filer's 10-K ends on its
# own fiscal year end, and its 10-Qs fall at quarter offsets from there. See
# structured/fiscal.py.
_IGNORED_FILTERS: set[str] = set()

# This graph holds a 10-K and one or two 10-Qs per filer, so a year's start
# cannot be read from the previous year's end the way the fact store does it.
# A year is taken as the 364 days before its 10-K, and a quarter as the 90
# before its 10-Q. Both are approximations and neither needs to be exact: the
# filter matches a filing's own reporting_period_end, and the quarter index is
# a rounded offset, so being a few days out changes nothing.
# Not a property on any node. These are turned into a reporting_period_end
# window by _fiscal_clause, so _check_filters has to know they are served.
_DERIVED_FILTERS = {"fiscal_year", "fiscal_period"}

_YEAR_SPAN = 364
_QUARTER_SPAN = 90
_CALENDAR = None

# Claim -> Section -> Filing -> Company, and every search walks all three.
TRAVERSAL_HOPS = 3

# Why each of the rest is refused, so the caller gets a reason and not a shrug.
_UNSUPPORTED_FILTERS = {
    "is_table": (
        "Claims are extracted from prose only, so every result would match "
        "is_table=False and nothing would match is_table=True"
    ),
    "max_hops": (
        f"this arm walks exactly {TRAVERSAL_HOPS} hops, Claim to Section to "
        "Filing to Company, and they run upward to fill the evidence "
        "envelope rather than outward to find anything. Fewer cannot name "
        "the filing a claim came from; more has nowhere to go. Depth is "
        "structural here, not a setting. A traversal whose depth can vary "
        "is neighbours(), which is not built yet"
    ),
}

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
        if (key in _FILTER_TO_PROPERTY or key in _IGNORED_FILTERS
                or key in _DERIVED_FILTERS):
            continue
        reason = _UNSUPPORTED_FILTERS.get(key)
        if reason:
            raise FilterNotSupportedError(f"the graph arm cannot filter on {key}: {reason}")
        raise FilterNotSupportedError(f"the graph arm cannot filter on {key}")


def _calendar(driver=None) -> dict:
    """Each filer's fiscal calendar, read once from the filings it holds."""
    global _CALENDAR
    if _CALENDAR is None:
        driver = driver or connect()
        with driver.session(database=_database) as session:
            rows = session.run(
                "MATCH (co:Company)-[:FILES]->(fl:Filing) "
                "WHERE co.ticker IS NOT NULL AND fl.reporting_period_end IS NOT NULL "
                "RETURN co.ticker AS ticker, fl.form AS form, "
                "fl.reporting_period_end AS period_end"
            ).data()
        periods = []
        for row in rows:
            end = fiscal._as_date(row["period_end"])
            if end is None:
                continue
            span = _YEAR_SPAN if str(row["form"]).upper().startswith("10-K") else _QUARTER_SPAN
            periods.append((row["ticker"], end - datetime.timedelta(days=span), end))
        _CALENDAR = fiscal.build_calendar(periods)
    return _CALENDAR


def _fiscal_clause(filters: dict) -> tuple[str, dict]:
    """Match filings whose own period end falls inside the label asked for.

    One window per filer, because FY2026 is a different stretch of time for
    each of them. No filer having the period is a real answer rather than a
    reason to drop the filter and return every year.

    Several periods make several windows joined by OR. A list containing FY
    widens to the whole year, because the FY window already contains every
    quarter in it.
    """
    if "fiscal_year" not in filters:
        raise FilterNotSupportedError(
            "fiscal_period needs fiscal_year: a quarter on its own names no "
            "stretch of time"
        )

    calendar = _calendar()
    wanted = filters["fiscal_year"]
    years = wanted if isinstance(wanted, (list, tuple, set)) else [wanted]
    scope = filters.get("ticker")
    if scope is None:
        tickers = sorted(calendar)
    else:
        tickers = list(scope) if isinstance(scope, (list, tuple, set)) else [scope]

    periods = fiscal.normalize_periods(filters.get("fiscal_period"))

    parts, params = [], {}
    for ticker in tickers:
        for year in years:
            for period in periods:
                span = fiscal.window(calendar, ticker, year, period)
                if not span:
                    continue
                key = f"fiscal_{len(parts)}"
                parts.append(
                    f"(co.ticker = ${key}_t AND "
                    f"fl.reporting_period_end >= ${key}_a AND "
                    f"fl.reporting_period_end <= ${key}_b)")
                params[f"{key}_t"] = ticker
                params[f"{key}_a"] = str(span[0])
                params[f"{key}_b"] = str(span[1])

    if not parts:
        return "false", {}
    return "(" + " OR ".join(parts) + ")", params


def _where_clause(filters: dict) -> tuple[str, dict]:
    """Turn the supported filters into Cypher and its parameters."""
    clauses, params = [], {}
    if "fiscal_year" in filters or "fiscal_period" in filters:
        clause, fiscal_params = _fiscal_clause(filters)
        clauses.append(clause)
        params.update(fiscal_params)
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
       // Lead sentences: the loader renamed risk_lead_unit_id / risk_lead_sentence
       // (Item 1A only) to lead_unit_id / is_lead_sentence (every section). Read
       // both until no graph holds the old names.
       coalesce(sup.lead_unit_id, sup.risk_lead_unit_id) AS lead_unit_id,
       sup.lead_source AS lead_source,
       coalesce(c.is_lead_sentence, c.risk_lead_sentence) AS is_lead_sentence,
       c.risk_summary AS risk_summary,
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
            # Lead sentences in every section (older graphs: Item 1A only, under the
            # old names, which the query maps to these). risk_summary: Item 1A only.
            "lead_unit_id": row.get("lead_unit_id"),
            "lead_source": row.get("lead_source"),
            "is_lead_sentence": row.get("is_lead_sentence"),
            "risk_summary": row.get("risk_summary"),
            "multi_fragment": row.get("multi_fragment"),
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

    try:
        with driver.session(database=_database) as session:
            if where:
                # Every claim the query matches, so the filter chooses from all
                # of them rather than from whichever happened to rank highest
                # across the whole graph. See FULLTEXT_MATCH_CAP.
                matched = session.run(
                    COUNT_MATCHES_CYPHER, index=FULLTEXT_INDEX, lucene=lucene,
                    limit=FULLTEXT_MATCH_CAP,
                ).single()["matches"]
                if matched >= FULLTEXT_MATCH_CAP:
                    # Say it. At the cap the filter is choosing from a prefix
                    # again, which is the defect this function exists to fix,
                    # and no count or row total reveals that it happened.
                    log.warning(
                        "fulltext match set reached FULLTEXT_MATCH_CAP (%d) for "
                        "query %r. The filter is choosing from a truncated "
                        "candidate list and rows may be missing. Raise the cap "
                        "or narrow the query.", FULLTEXT_MATCH_CAP, query,
                    )
                limit = max(matched, top_k)
            else:
                # Nothing is discarded after the index, so the best top_k the
                # index returns are the best top_k there are.
                limit = top_k

            rows = session.run(
                cypher, index=FULLTEXT_INDEX, lucene=lucene, limit=limit, **params
            ).data()
    except contract.RetrievalUnavailableError:
        raise
    except Exception as exc:
        raise contract.RetrievalUnavailableError(f"graph query failed: {exc}") from exc

    return [_to_evidence(row, trace_id) for row in rows[:top_k]]
