"""Mock graph_search and structured_search for INT-0.

Standalone and standard-library only, so other teams can drop this file
in without installing anything. Same signature, filters, evidence shape,
ordering and errors as the real tools will have.

The ranking is keyword overlap. Its quality is meaningless; only the
shape is guaranteed. Swap the import when the real tools land:

    from graphrag.retrieve import graph_search, structured_search

Content comes from Microsoft's 10-Q, accession 0001193125-26-191507,
the same filing Eval's gold set is built on, so values here match their
reference answers.

`trace_id` is optional and opaque. We echo it at `metadata.trace_id` on
every result so Eval can join back to a benchmark question id.
"""

from __future__ import annotations

import re

MAX_TOP_K = 50

SHARED_FILTERS = {
    "ticker", "cik", "form", "accession_number", "filing_date",
    "reporting_period_end", "fiscal_year", "fiscal_period", "section", "is_table",
}
GRAPH_FILTERS = SHARED_FILTERS | {"max_hops"}
STRUCTURED_FILTERS = SHARED_FILTERS | {"concept", "segment", "basis"}
RENAMED_FILTERS = {
    "form_type": "form",
    "accession": "accession_number",
    "period_end": "reporting_period_end",
    "question": "query",
}


class RetrievalUnavailableError(RuntimeError):
    """The store could not be reached. Distinct from 'nothing matched'."""


_FILING = {
    "ticker": "MSFT",
    "cik": "0000789019",
    "company_name": "Microsoft Corporation",
    "form": "10-Q",
    "accession_number": "0001193125-26-191507",
    "filing_date": "2026-04-29",
    "reporting_period_end": "2026-03-31",
    "source_url": (
        "https://www.sec.gov/Archives/edgar/data/789019/"
        "000119312526191507/msft-20260331.htm"
    ),
}

_GRAPH_ROWS = [
    {
        "section_id": "sec-msft-26q3-note16",
        "section": "Part I Item 1",
        "section_title": "Segment Information and Geographic Data",
        "text": (
            "Microsoft reports three segments: Productivity and Business Processes, "
            "Intelligent Cloud, and More Personal Computing."
        ),
        "quote": "Productivity and Business Processes, Intelligent Cloud, and More Personal Computing",
        "path": ["Microsoft Corporation", "HAS_SEGMENT", "Intelligent Cloud"],
        "hops": 1,
        "edge_types": ["HAS_SEGMENT"],
    },
    {
        "section_id": "sec-msft-26q3-note16",
        "section": "Part I Item 1",
        "section_title": "Segment Information and Geographic Data",
        "text": (
            "Intelligent Cloud revenue was $34,681 million for the three months "
            "ended March 31, 2026, with operating income of $13,753 million."
        ),
        "quote": "Intelligent Cloud Revenue $ 34,681",
        "path": ["Intelligent Cloud", "SCOPES", "Revenue FY2026 Q3"],
        "hops": 2,
        "edge_types": ["HAS_SEGMENT", "SCOPES"],
    },
    {
        "section_id": "sec-msft-26q3-mda",
        "section": "Part I Item 2",
        "section_title": "Management's Discussion and Analysis",
        "text": (
            "Microsoft Cloud revenue grew across Azure and other cloud services, "
            "Office 365 Commercial, and the commercial portion of LinkedIn."
        ),
        "quote": "Microsoft Cloud revenue grew across Azure and other cloud services",
        "path": ["Microsoft Corporation", "DISCUSSES", "Microsoft Cloud"],
        "hops": 1,
        "edge_types": ["DISCUSSES"],
    },
]

_FACT_ROWS = [
    {
        "fact_id": "fact-msft-26q3-revenue-total",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "value_num": 82886,
        "value_raw": "82,886",
        "segment": None,
        "text": "Total revenue, three months ended 2026-03-31: USD 82,886 million",
    },
    {
        "fact_id": "fact-msft-26q3-revenue-ic",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "value_num": 34681,
        "value_raw": "34,681",
        "segment": "Intelligent Cloud",
        "text": "Intelligent Cloud revenue, three months ended 2026-03-31: USD 34,681 million",
    },
    {
        "fact_id": "fact-msft-26q3-rnd",
        "concept": "us-gaap:ResearchAndDevelopmentExpense",
        "value_num": 8915,
        "value_raw": "8,915",
        "segment": None,
        "text": "Research and development, three months ended 2026-03-31: USD 8,915 million",
    },
]


def _validate(query, filters, top_k, allowed, trace_id):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    if top_k > MAX_TOP_K:
        raise ValueError(f"top_k must be <= {MAX_TOP_K}")
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip()):
        raise ValueError("trace_id must be a non-empty string or None")
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
    return {k: v for k, v in filters.items() if v is not None}


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _overlap(query, text):
    q = _tokens(query)
    if not q:
        return 0.0
    return round(len(q & _tokens(text)) / len(q), 6)


def _matches(active, row_section, extra=None):
    """Apply the filters a mock can honestly honour."""
    for key, want in active.items():
        if key in ("max_hops", "is_table", "fiscal_year", "fiscal_period"):
            continue
        if key == "section":
            # Whole-word prefix on the canonical key, as RAG does.
            want_list = want if isinstance(want, list) else [want]
            if not any(
                row_section == w or row_section.startswith(w.rstrip() + " ")
                for w in want_list
            ):
                return False
            continue
        if key in ("concept", "segment", "basis"):
            if extra is None:
                return False
            got = extra.get(key)
            want_list = want if isinstance(want, list) else [want]
            if got not in want_list:
                return False
            continue
        got = _FILING.get(key)
        want_list = want if isinstance(want, list) else [want]
        if key == "cik":
            want_list = [str(w).zfill(10) for w in want_list]
        if isinstance(got, str):
            if not any(isinstance(w, str) and got.lower() == w.lower() for w in want_list):
                return False
        elif got not in want_list:
            return False
    return True


def _envelope(*, section_id, chunk_id, text, quote, score, section, metadata):
    item = dict(_FILING)
    item.update({
        "rank": 0,                      # set after sorting
        "chunk_id": chunk_id,
        "section_id": section_id,
        "text": text,
        "quote": quote,
        "score": score,
        "section": section,
        "chunking_version": None,       # we do not chunk
        "extraction_version": "mock-v0",
        "index_version": "mock-int0",
        "metadata": metadata,
    })
    return item


def _finish(rows, top_k):
    rows.sort(key=lambda r: (-r["score"], r["section_id"] or r["metadata"]["fact_id"]))
    rows = rows[:top_k]
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def graph_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> list[dict]:
    active = _validate(query, filters, top_k, GRAPH_FILTERS, trace_id)
    max_hops = active.get("max_hops", 3)

    out = []
    for row in _GRAPH_ROWS:
        if not _matches(active, row["section"]):
            continue
        if row["hops"] > max_hops:
            continue
        score = _overlap(query, row["text"] + " " + row["section_title"])
        if score <= 0:
            continue
        out.append(_envelope(
            section_id=row["section_id"],
            chunk_id=None,
            text=row["text"],
            quote=row["quote"],
            score=score,
            section=row["section"],
            metadata={
                "trace_id": trace_id,
                "section_title": row["section_title"],
                "corpus_version": "mock",
                "path": row["path"],
                "hops": row["hops"],
                "edge_types": row["edge_types"],
                "graph_version": "mock-int0",
                "retrieval_method": "mock_keyword_overlap",
            },
        ))
    return _finish(out, top_k)


def structured_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> list[dict]:
    active = _validate(query, filters, top_k, STRUCTURED_FILTERS, trace_id)

    out = []
    for row in _FACT_ROWS:
        extra = {"concept": row["concept"], "segment": row["segment"], "basis": "as_filed"}
        if not _matches(active, "Part I Item 1", extra=extra):
            continue
        score = _overlap(query, row["text"])
        if score <= 0:
            continue
        out.append(_envelope(
            section_id=None,
            chunk_id=None,
            text=row["text"],
            quote=None,
            score=score,
            section="Part I Item 1",
            metadata={
                "trace_id": trace_id,
                "section_title": "Financial Statements",
                "corpus_version": "mock",
                "fact_id": row["fact_id"],
                "concept": row["concept"],
                "context_ref": "c-14",
                "unit_ref": "iso4217:USD",
                "basis": "as_filed",
                "value_raw": row["value_raw"],
                "value_num": row["value_num"],
                "period_start": "2026-01-01",
                "period_end": "2026-03-31",
                "dimensions": (
                    {"StatementBusinessSegmentsAxis": row["segment"]}
                    if row["segment"] else {}
                ),
                "retrieval_method": "mock_keyword_overlap",
            },
        ))
    return _finish(out, top_k)
