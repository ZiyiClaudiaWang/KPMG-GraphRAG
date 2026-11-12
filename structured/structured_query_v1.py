import os
import duckdb
import json

import concept_match
import segments

# Set once per connection from the store itself, so the envelope describes
# what is actually loaded instead of what someone typed.
_STORE_PATH = None
_FILINGS = {}
_CORPUS_VERSION = None


def _store_path():
    path = os.environ.get("MSFT_FACT_DB_PATH")
    if not path:
        raise ValueError(
            "Set MSFT_FACT_DB_PATH to the path of a rebuilt DuckDB fact store."
        )
    return path


def load_store_metadata(conn=None):
    """Read who filed what, from the store.

    `company_name`, `form` and `corpus_version` used to be literals reading
    "Microsoft Corporation", "10-K" and "msft-fy2025-10k". With ten companies
    and a mix of annual and quarterly filings in the corpus, every envelope
    claimed to be a Microsoft 10-K. `validate_results()` checks shape, not
    whether a label agrees with the accession beside it, so the wrong one
    travelled.

    Stores written before the loader recorded this have no tables, which is
    not an error: the fields fall back to None and the envelope says it does
    not know rather than guessing.
    """
    global _STORE_PATH, _FILINGS, _CORPUS_VERSION
    path = _store_path()
    close_after = conn is None
    if conn is None:
        conn = duckdb.connect(path, read_only=True)
    try:
        try:
            rows = conn.execute(
                "SELECT accession_number, company_name, form FROM filings_v1"
            ).fetchall()
            _FILINGS = {r[0]: {"company_name": r[1], "form": r[2]} for r in rows}
        except duckdb.CatalogException:
            _FILINGS = {}
        try:
            row = conn.execute(
                "SELECT value FROM store_meta_v1 WHERE key = 'corpus_version'"
            ).fetchone()
            _CORPUS_VERSION = row[0] if row else None
        except duckdb.CatalogException:
            _CORPUS_VERSION = None
    finally:
        if close_after:
            conn.close()
    _STORE_PATH = path


def _filing(accession_number):
    if _STORE_PATH != _store_path():
        load_store_metadata()
    if not _FILINGS:
        # The contract does not allow a null company_name or form, so a store
        # built before the loader recorded them cannot produce a valid
        # envelope. Say that, rather than leaving the caller with
        # "evidence field may not be null: company_name".
        raise RuntimeError(
            f"{_store_path()} has no filings_v1 table, so company_name and form "
            "are unknown. Rebuild it with structured/fact_loader_v1.py."
        )
    return _FILINGS.get(accession_number, {})

# The ten keys the contract allows every tool, plus our three. Kept the same
# as `graphrag/contract.py` STRUCTURED_FILTERS, and `tests/test_structured_
# filters.py` fails if the two drift. A key the contract permits must not raise
# here: Eval's gold set fills `filters` from the contract, and 148 of its 155
# questions carry form, fiscal_year or fiscal_period.
SUPPORTED_FILTERS = {
    "ticker", "cik", "form", "accession_number", "filing_date",
    "reporting_period_end", "fiscal_year", "fiscal_period", "section",
    "is_table", "concept", "segment", "basis",
}

# Accepted and not applied, which is what the mock already does.
#
#   fiscal_year, fiscal_period  a fiscal year is not a calendar year and the
#                               year end moves by company. Deriving one needs a
#                               fiscal calendar we do not hold.
#   section, is_table           an XBRL fact has no section pointer. That is
#                               issue #40, and until it closes there is nothing
#                               to filter on.
#
# Ignoring a filter silently is its own hazard: the caller gets unfiltered rows
# that look filtered. Keep this list short, and say so in the reply to Eval
# whenever it changes.
IGNORED_FILTERS = {"fiscal_year", "fiscal_period", "section", "is_table"}

# Filters that take one value only. Everything else may be given a list, which
# becomes an IN. `segment` and `basis` are looked up in a table of known names
# rather than compared, so a list has nowhere to go.
SINGLE_VALUE_FILTERS = {"segment", "basis"}


def _match_one_or_many(key, column, value):
    """`column = ?` for one value, `column IN (?, ?)` for several.

    A list used to reach DuckDB as a single parameter and fail as a cast error,
    which the wrapper then reported as RetrievalUnavailableError -- an outage,
    telling the caller to retry something that could never work.
    """
    if not isinstance(value, (list, tuple, set)):
        return f" AND {column} = ?", [value]

    values = list(value)
    if not values:
        raise ValueError(f"{key} is an empty list; give at least one value or drop the filter")
    for item in values:
        if isinstance(item, (list, tuple, set, dict)):
            raise ValueError(f"{key} may be a list of values, but not a list of lists")

    placeholders = ", ".join("?" for _ in values)
    return f" AND {column} IN ({placeholders})", values

# Old names that turned up in earlier drafts. Hint rather than guess.
RENAMED_FILTERS = {
    "form_type": "form",
    "accession": "accession_number",
    "period_end": "reporting_period_end",
    "question": "query",
}


def _filings_table_exists(conn=None):
    """Whether this store carries filings_v1. Old stores predate it."""
    close_after = conn is None
    if conn is None:
        conn = duckdb.connect(_store_path(), read_only=True)
    try:
        conn.execute("SELECT 1 FROM filings_v1 LIMIT 1")
        return True
    except duckdb.CatalogException:
        return False
    finally:
        if close_after:
            conn.close()


def query_database(query, filters=None, top_k=10, trace_id=None):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError("top_k must be an integer from 1 to 50")
    if trace_id is not None and (
        not isinstance(trace_id, str) or not trace_id.strip()
    ):
        raise ValueError("trace_id must be a non-empty string or None")
    if filters is not None and not isinstance(filters, dict):
        raise ValueError("filters must be a dict or None")

    filters = filters or {}
    for key, value in filters.items():
        if key not in SUPPORTED_FILTERS:
            hint = RENAMED_FILTERS.get(key)
            message = f"unsupported filter: {key}"
            if hint:
                message += f" (use '{hint}')"
            raise ValueError(message)
        if key in SINGLE_VALUE_FILTERS and isinstance(value, (list, tuple, set)):
            raise ValueError(f"{key} takes one value, not a list")

    sql = "SELECT * FROM financial_facts_v1 WHERE 1=1"
    params = []

    for key, column in {
        "ticker": "ticker",
        "cik": "cik",
        "accession_number": "accession_number",
        "concept": "concept_name",
        "reporting_period_end": "period_end",
        "filing_date": "filing_date"
    }.items():
        if key in filters:
            clause, values = _match_one_or_many(key, column, filters[key])
            sql += clause
            params.extend(values)

    if "form" in filters:
        # form lives on the filing, not the fact. filings_v1 is required for a
        # valid envelope anyway, so a store without it cannot serve this.
        if not _filings_table_exists():
            raise RuntimeError(
                f"{_store_path()} has no filings_v1 table, so the form filter "
                "cannot be applied. Rebuild it with structured/fact_loader_v1.py."
            )
        clause, values = _match_one_or_many("form", "form", filters["form"])
        sql += f""" AND accession_number IN (
            SELECT accession_number FROM filings_v1 WHERE 1=1{clause}
        )"""
        params.extend(values)

    if "basis" in filters:
        if filters["basis"] != "as_filed":
            raise ValueError(
                "Only as_filed is available; no restated filing is loaded"
            )

    path = _store_path()
    conn = duckdb.connect(path, read_only=True)
    try:
        # The segment members come from the store, so this needs the
        # connection open before the SQL is finished.
        if "segment" in filters:
            members = segments.resolve(conn, filters["segment"], path)
            placeholders = ", ".join("?" for _ in members)
            sql += f""" AND EXISTS (
                SELECT 1 FROM json_each(financial_facts_v1.dimensions) AS d
                WHERE d.value ->> 'axis' = '{segments.SEGMENT_AXIS}'
                  AND (d.value ->> 'member') IN ({placeholders})
            )"""
            params.extend(members)

        # fact_id is a hash, so this orders nothing. It is here to make the
        # result set stable between runs; the ranking happens in
        # rank_records() below, after the question has been read.
        sql += " ORDER BY fact_id"

        return conn.execute(sql, params).df().to_dict("records")
    finally:
        conn.close()


def rank_records(records, query, filters, top_k):
    """Order the rows the filters allowed by how well they answer the question.

    The caller can mean two different things, so this honours both:

    - **A question.** Rows are scored on the question's words and anything
      scoring zero is dropped, because returning a fact that has nothing to do
      with the question is worse than returning nothing. An empty list is a
      real answer under the contract.
    - **An explicit `concept` filter.** The caller has already named what they
      want, which is a stronger statement than any wording of the question.
      Scoring is skipped and every row is kept.
    """
    if filters.get("concept"):
        return [(1.0, r) for r in records][:top_k]

    terms = concept_match.question_terms(query)
    alias = concept_match.aliased_concept(terms)

    scored = []
    filers = {}
    for r in records:
        ticker = r.get("ticker")
        if ticker not in filers:
            try:
                name = _filing(r["accession_number"]).get("company_name")
            except (KeyError, RuntimeError):
                # Ranking still works off the ticker alone. A store too old to
                # name its filers is not a reason to refuse to order rows.
                name = None
            filers[ticker] = concept_match.filer_words(ticker, name)
        score = concept_match.score_fact(
            terms, r["concept_name"], r.get("dimensions"), alias, filers[ticker]
        )
        if score > 0:
            scored.append((score, r))

    # Equal scores are common, and until now the fact id decided them, which
    # is a hash. A statement prints the quarter and the year-to-date column
    # together, so "revenue for the three months ended March 31" tied with the
    # nine-month figure ending the same day and either could win.
    #
    # Three tie-breaks before the hash, in the order Eval asked for them:
    #   1. the period the question named, if it named one
    #   2. the consolidated fact over a slice of it
    #   3. the newest period
    asked = concept_match.asked_duration_days(query)
    scored.sort(key=lambda pair: (
        -pair[0],
        0 if concept_match.duration_matches(
            asked, concept_match.duration_days(pair[1].get("period_start"),
                                               pair[1].get("period_end"))) else 1,
        len(concept_match._dimension_entries(pair[1].get("dimensions"))),
        _period_sort_key(pair[1]),
        pair[1]["fact_id"],
    ))
    return scored[:top_k]


def _period_sort_key(record):
    """Newest period first.

    Returns a negative number so it sorts descending alongside the score.
    A missing or unparseable date returns 0 and lands last.
    """
    digits = "".join(c for c in str(record.get("period_end") or "") if c.isdigit())
    return -int(digits) if digits else 0


def structured_search(query, filters=None, top_k=10, trace_id=None):
    records = query_database(query, filters, top_k, trace_id)
    ranked = rank_records(records, query, filters or {}, top_k)
    evidence = []

    for rank, (score, r) in enumerate(ranked, 1):
        evidence.append({
            "rank": rank,
            "company_name": _filing(r["accession_number"]).get("company_name"),
            "filing_date": r["filing_date"],
            "reporting_period_end": r["period_end"],
            "source_url": None,
            "section": None,
            "form": _filing(r["accession_number"]).get("form"),
            "chunking_version": None,
            "extraction_version": "structured-loader-v1",
            "index_version": "duckdb-v1",
            "ticker": r["ticker"],
            "cik": r["cik"],
            "accession_number": r["accession_number"],
            "text": f"{r['concept_name']}: {r['numeric_value']} {r['unit']}",
            "quote": None,
            "score": score,
            "section_id": None,
            "chunk_id": None,
            "metadata": {
                "trace_id": trace_id,
                "corpus_version": _CORPUS_VERSION,
                "basis": "as_filed",
                "section_title": None,
                "retrieval_method": "duckdb_concept_match",
                "fact_id": r["fact_id"],
                "concept": r["concept_name"],
                "context_ref": r["context_ref"],
                "unit_ref": r["unit_ref"],
                "value_raw": r["raw_value"],
                "value_num": float(r["numeric_value"]) if r["numeric_value"] is not None else None,
                "period_start": r["period_start"],
                "period_end": r["period_end"],
                "dimensions": json.loads(r["dimensions"])
            }
        })

    return evidence
