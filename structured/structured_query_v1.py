import os
import duckdb
import json

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

    sql += " ORDER BY fact_id LIMIT ?"
    params.append(top_k)

    conn = duckdb.connect(
        os.environ.get("MSFT_FACT_DB_PATH", "msft_financial_facts_v1.duckdb"),
        read_only=True
    )
    try:
        return conn.execute(sql, params).df().to_dict("records")
    finally:
        conn.close()


def structured_search(query, filters=None, top_k=10, trace_id=None):
    records = query_database(query, filters, top_k, trace_id)
    evidence = []

    for rank, r in enumerate(records, 1):
        evidence.append({
            "rank": rank,
            "company_name": "Microsoft Corporation",
            "filing_date": None,
            "reporting_period_end": "2025-06-30",
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
            "score": 1.0,
            "section_id": None,
            "chunk_id": None,
            "metadata": {
                "trace_id": trace_id,
                "corpus_version": "msft-fy2025-10k",
                "basis": "as_filed",
                "section_title": None,
                "retrieval_method": "duckdb_exact_filter",
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
