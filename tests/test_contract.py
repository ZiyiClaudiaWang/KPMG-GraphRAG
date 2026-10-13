"""Conformance tests for the INT-0 tool contract.

These run against the mock today and against the real tools when they
land, so code built on the mock keeps working after the swap.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mock"))

from graphrag import contract  # noqa: E402
from mock_retrieval_tools import graph_search, structured_search  # noqa: E402

TOOLS = [
    pytest.param(graph_search, contract.GRAPH_FILTERS, id="graph"),
    pytest.param(structured_search, contract.STRUCTURED_FILTERS, id="structured"),
]

QUERY = "Intelligent Cloud segment revenue"


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_returns_valid_evidence(tool, allowed):
    results = tool(QUERY, top_k=10)
    assert results, "mock should match this query"
    contract.validate_results(results, top_k=10)


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_respects_top_k(tool, allowed):
    assert len(tool(QUERY, top_k=1)) <= 1


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_deterministic(tool, allowed):
    assert tool(QUERY) == tool(QUERY)


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_no_match_returns_empty_list(tool, allowed):
    out = tool("zzzz nonexistent token")
    assert out == []


@pytest.mark.parametrize("tool,allowed", TOOLS)
@pytest.mark.parametrize("bad", ["", "   ", None, 7])
def test_bad_query_raises(tool, allowed, bad):
    with pytest.raises(ValueError, match="query must be"):
        tool(bad)


@pytest.mark.parametrize("tool,allowed", TOOLS)
@pytest.mark.parametrize("bad", [0, -5, 2.5, True, "10"])
def test_bad_top_k_raises(tool, allowed, bad):
    with pytest.raises(ValueError, match="top_k must be"):
        tool(QUERY, top_k=bad)


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_top_k_ceiling(tool, allowed):
    with pytest.raises(ValueError, match="top_k must be <="):
        tool(QUERY, top_k=51)


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_unknown_filter_raises(tool, allowed):
    with pytest.raises(ValueError, match="unsupported filter: nonsense"):
        tool(QUERY, filters={"nonsense": 1})


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_renamed_filter_gets_a_hint(tool, allowed):
    with pytest.raises(ValueError, match=r"use 'form'"):
        tool(QUERY, filters={"form_type": "10-Q"})


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_none_valued_filter_is_ignored(tool, allowed):
    assert tool(QUERY, filters={"ticker": None}) == tool(QUERY)


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_filters_must_be_a_dict(tool, allowed):
    with pytest.raises(ValueError, match="filters must be a dict"):
        tool(QUERY, filters=["ticker"])


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_cik_accepts_unpadded_input(tool, allowed):
    assert tool(QUERY, filters={"cik": 789019}) == tool(QUERY, filters={"cik": "0000789019"})


@pytest.mark.parametrize("tool,allowed", TOOLS)
def test_non_matching_filter_returns_empty(tool, allowed):
    assert tool(QUERY, filters={"ticker": "AAPL"}) == []


def test_graph_populates_section_id_not_chunk_id():
    for item in graph_search(QUERY):
        assert item["section_id"] is not None
        assert item["chunk_id"] is None
        assert "fact_id" not in item["metadata"]


def test_structured_populates_fact_id_only():
    for item in structured_search("revenue"):
        assert item["section_id"] is None
        assert item["chunk_id"] is None
        assert item["metadata"]["fact_id"]


def test_max_hops_narrows_the_graph():
    wide = graph_search(QUERY, filters={"max_hops": 3})
    narrow = graph_search(QUERY, filters={"max_hops": 1})
    assert len(narrow) <= len(wide)
    assert all(i["metadata"]["hops"] <= 1 for i in narrow)


def test_section_filter_is_whole_word_prefix():
    assert graph_search("Microsoft Cloud revenue", filters={"section": "Part I"})
    assert graph_search("Microsoft Cloud revenue", filters={"section": "Part I Item 2"})
    assert graph_search("Microsoft Cloud revenue", filters={"section": "Part II"}) == []


def test_dimensional_facts_are_distinguishable_from_totals():
    """A total and a part share a concept and a period. Only the
    dimension separates them, and summing both double-counts."""
    rows = structured_search("revenue three months ended")
    by_id = {r["metadata"]["fact_id"]: r["metadata"] for r in rows}
    total = by_id["fact-msft-26q3-revenue-total"]
    part = by_id["fact-msft-26q3-revenue-ic"]
    assert total["concept"] == part["concept"]
    assert total["period_end"] == part["period_end"]
    assert total["dimensions"] == {}
    assert part["dimensions"]["StatementBusinessSegmentsAxis"] == "Intelligent Cloud"


def test_evidence_rejects_two_provenance_keys():
    item = graph_search(QUERY)[0]
    item["chunk_id"] = "also-a-chunk"
    with pytest.raises(ValueError, match="exactly one of"):
        contract.validate_evidence(item)


def test_evidence_rejects_undocumented_fields():
    item = graph_search(QUERY)[0]
    item["surprise"] = 1
    with pytest.raises(ValueError, match="undocumented fields"):
        contract.validate_evidence(item)
