"""Shape and validation for what our retrieval tools return.

Mirrors RAG's `rag/contract.py` so the Agent, UI and Eval see one shape
across all three tools. Field names come from their Retrieval API v0.

Our two tools differ from theirs in one way only: `chunk_id` is null,
because the graph arm cites sections and the structured arm cites facts.
Exactly one of `chunk_id`, `section_id` or `metadata.fact_id` identifies
any evidence object.
"""

from __future__ import annotations

# Every evidence object carries all of these. Only the ones listed as
# nullable below may be None.
EVIDENCE_FIELDS = (
    "rank",
    "chunk_id",
    "section_id",
    "text",
    "quote",
    "score",
    "section",
    "ticker",
    "cik",
    "company_name",
    "form",
    "accession_number",
    "filing_date",
    "reporting_period_end",
    "source_url",
    "chunking_version",
    "extraction_version",
    "index_version",
    "metadata",
)

NULLABLE_FIELDS = frozenset({
    "chunk_id",
    "section_id",
    "quote",
    "section",
    "ticker",
    "reporting_period_end",
    "source_url",
    "chunking_version",
})

# RAG's ten filter keys, plus ours. Anything else is an error rather
# than a silently widened search.
SHARED_FILTERS = frozenset({
    "ticker",
    "cik",
    "form",
    "accession_number",
    "filing_date",
    "reporting_period_end",
    "fiscal_year",
    "fiscal_period",
    "section",
    "is_table",
})
GRAPH_FILTERS = SHARED_FILTERS | {"max_hops"}
STRUCTURED_FILTERS = SHARED_FILTERS | {"concept", "segment", "basis"}

# Old names that turned up in earlier drafts. Hint rather than guess.
RENAMED_FILTERS = {
    "form_type": "form",
    "accession": "accession_number",
    "period_end": "reporting_period_end",
    "period_start": "reporting_period_end",
    "question": "query",
}

MAX_TOP_K = 50


class RetrievalUnavailableError(RuntimeError):
    """The store could not be reached. Distinct from 'nothing matched'."""


def validate_query(query: object) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    return query


def validate_top_k(top_k: object) -> int:
    # bool is an int in Python, and True would silently mean top_k=1.
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    if top_k > MAX_TOP_K:
        raise ValueError(f"top_k must be <= {MAX_TOP_K}")
    return top_k


def validate_filters(filters: object, allowed: frozenset[str]) -> dict:
    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ValueError("filters must be a dict or None")
    for key in filters:
        if key in allowed:
            continue
        hint = RENAMED_FILTERS.get(key)
        if hint:
            raise ValueError(f"unsupported filter: {key} (use '{hint}')")
        raise ValueError(f"unsupported filter: {key}")
    # A key set to None is ignored, so the Agent can always send the full dict.
    return {k: v for k, v in filters.items() if v is not None}


def validate_evidence(item: object) -> None:
    """Raise if an evidence object does not match the contract."""
    if not isinstance(item, dict):
        raise ValueError("evidence item must be a dict")

    missing = [f for f in EVIDENCE_FIELDS if f not in item]
    if missing:
        raise ValueError(f"evidence missing fields: {', '.join(missing)}")

    extra = set(item) - set(EVIDENCE_FIELDS)
    if extra:
        raise ValueError(f"evidence has undocumented fields: {', '.join(sorted(extra))}")

    for field in EVIDENCE_FIELDS:
        if item[field] is None and field not in NULLABLE_FIELDS:
            raise ValueError(f"evidence field may not be null: {field}")

    if not isinstance(item["metadata"], dict):
        raise ValueError("evidence metadata must be a dict")

    cik = item["cik"]
    if not (isinstance(cik, str) and len(cik) == 10 and cik.isdigit()):
        raise ValueError("cik must be a 10-digit zero-padded string")

    # Exactly one provenance key. Specced once, it gets specced for
    # chunks, and two of three arms end up with a null key.
    keys = [
        item["chunk_id"],
        item["section_id"],
        item["metadata"].get("fact_id"),
    ]
    populated = [k for k in keys if k is not None]
    if len(populated) != 1:
        raise ValueError(
            "exactly one of chunk_id, section_id or metadata.fact_id must be set, "
            f"got {len(populated)}"
        )


def validate_results(results: object, top_k: int) -> None:
    """Raise if a tool's return value does not match the contract."""
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    if len(results) > top_k:
        raise ValueError(f"returned {len(results)} results, top_k was {top_k}")

    for item in results:
        validate_evidence(item)

    ranks = [item["rank"] for item in results]
    if ranks != list(range(1, len(results) + 1)):
        raise ValueError("rank must run 1..n in list order")

    scores = [item["score"] for item in results]
    if scores != sorted(scores, reverse=True):
        raise ValueError("results must be ordered by score descending")


def sort_key(item: dict) -> tuple:
    """Deterministic ordering: score descending, then the provenance key.

    RAG ties on chunk_id. Our arms have none, so we fall through to
    section_id and then fact_id.
    """
    tiebreak = (
        item.get("chunk_id")
        or item.get("section_id")
        or item.get("metadata", {}).get("fact_id")
        or ""
    )
    return (-item["score"], tiebreak)
