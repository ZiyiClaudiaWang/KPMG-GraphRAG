import os
import duckdb
import json

import concept_match

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

    if "segment" in filters:
        segments = {
            "Intelligent Cloud": "msft:IntelligentCloudMember",
            "More Personal Computing": "msft:MorePersonalComputingMember",
            "Productivity and Business Processes":
                "msft:ProductivityAndBusinessProcessesMember"
        }
        member = segments.get(filters["segment"])
        if member is None:
            raise ValueError("Unknown segment: " + str(filters["segment"]))
        sql += """ AND EXISTS (
            SELECT 1 FROM json_each(financial_facts_v1.dimensions) AS d
            WHERE d.value ->> 'axis' = 'us-gaap:StatementBusinessSegmentsAxis'
              AND (d.value ->> 'member') = ?
        )"""
        params.append(member)

    if "basis" in filters:
        if filters["basis"] != "as_filed":
            raise ValueError(
                "Only as_filed is available; no restated filing is loaded"
            )

    # fact_id is a hash, so this orders nothing. It is here to make the result
    # set stable between runs; the ranking happens in rank_records() below,
    # after the question has been read.
    sql += " ORDER BY fact_id"

    conn = duckdb.connect(
        os.environ.get("MSFT_FACT_DB_PATH", "msft_financial_facts_v1.duckdb"),
        read_only=True
    )
    try:
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
    for r in records:
        score = concept_match.score_fact(
            terms, r["concept_name"], r.get("dimensions"), alias
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
            "company_name": "Microsoft Corporation",
            "filing_date": r["filing_date"],
            "reporting_period_end": r["period_end"],
            "source_url": None,
            "section": None,
            "form": "10-K",
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
                "corpus_version": "msft-fy2025-10k",
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
