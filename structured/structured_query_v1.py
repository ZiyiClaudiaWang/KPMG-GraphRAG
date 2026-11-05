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
    return os.environ.get("MSFT_FACT_DB_PATH", "msft_financial_facts_v1.duckdb")


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
    supported = {
        "ticker", "cik", "accession_number",
        "concept", "reporting_period_end", "segment", "basis"
    }
    renamed = {
        "form_type": "form",
        "accession": "accession_number",
        "period_end": "reporting_period_end",
        "question": "query"
    }
    for key in filters:
        if key not in supported:
            hint = renamed.get(key)
            message = f"unsupported filter: {key}"
            if hint:
                message += f" (use '{hint}')"
            raise ValueError(message)

    sql = "SELECT * FROM financial_facts_v1 WHERE 1=1"
    params = []

    for key, column in {
        "ticker": "ticker",
        "cik": "cik",
        "accession_number": "accession_number",
        "concept": "concept_name",
        "reporting_period_end": "period_end"
    }.items():
        if key in filters:
            sql += f" AND {column} = ?"
            params.append(filters[key])

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

    # Equal scores are common: the same concept reported for three fiscal
    # years ties exactly. Newest first is the more useful answer, and the
    # fact id after that keeps the order identical between runs.
    scored.sort(key=lambda pair: (-pair[0],
                                  _period_sort_key(pair[1]),
                                  pair[1]["fact_id"]))
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
