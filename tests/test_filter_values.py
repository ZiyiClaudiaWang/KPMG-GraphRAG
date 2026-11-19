"""A filter value the arm cannot use is the caller's error, not an outage.

Eval hit both halves of this on round 1: list-valued `ticker` on 13
cross-company questions raised RetrievalUnavailableError from the structured
arm (a DuckDB cast error, surfaced as a dead store) and returned nothing at
all from the graph arm. One told them to retry a call that could never work;
the other told them there was no evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "structured"))

from graphrag import contract, graph_backend, retrieve  # noqa: E402


@pytest.fixture(autouse=True)
def clear_backends():
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)
    yield
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)


# ---- the graph arm builds an IN ------------------------------------------

def test_a_list_becomes_an_in_rather_than_matching_nothing():
    where, params = graph_backend._where_clause({"ticker": ["MSFT", "AAPL"]})
    assert where == "co.ticker IN $ticker"
    assert params == {"ticker": ["MSFT", "AAPL"]}


def test_one_value_still_compares_with_equals():
    where, params = graph_backend._where_clause({"ticker": "MSFT"})
    assert where == "co.ticker = $ticker"
    assert params == {"ticker": "MSFT"}


def test_an_empty_list_is_refused_rather_than_matching_everything():
    with pytest.raises(ValueError, match="empty list"):
        graph_backend._where_clause({"ticker": []})


# ---- the structured arm builds an IN -------------------------------------

def test_structured_builds_an_in_for_several_values():
    import structured_query_v1 as sq

    clause, values = sq._match_one_or_many("ticker", "ticker", ["MSFT", "AAPL"])
    assert clause == " AND ticker IN (?, ?)"
    assert values == ["MSFT", "AAPL"]


def test_structured_keeps_equals_for_one_value():
    import structured_query_v1 as sq

    clause, values = sq._match_one_or_many("ticker", "ticker", "MSFT")
    assert clause == " AND ticker = ?"
    assert values == ["MSFT"]


def test_structured_refuses_a_list_of_lists():
    import structured_query_v1 as sq

    with pytest.raises(ValueError, match="not a list of lists"):
        sq._match_one_or_many("ticker", "ticker", [["MSFT"]])


def test_a_filter_that_takes_one_value_says_so():
    import structured_query_v1 as sq

    assert "segment" in sq.SINGLE_VALUE_FILTERS
    assert "basis" in sq.SINGLE_VALUE_FILTERS


# ---- and the wrapper lets it through -------------------------------------

def test_a_bad_filter_value_is_not_reported_as_an_outage():
    """The wrapper re-raises ValueError instead of wrapping it. An unknown
    filter *key* already raised ValueError before any backend was called, so a
    bad *value* for a known key should land the same way."""
    def backend(query, filters, top_k, trace_id):
        raise ValueError("ticker is an empty list")

    retrieve.configure_graph_backend(backend)
    with pytest.raises(ValueError, match="empty list"):
        retrieve.graph_search("revenue", filters={"ticker": []})


def test_a_dead_store_is_still_an_outage():
    def backend(query, filters, top_k, trace_id):
        raise RuntimeError("connection reset")

    retrieve.configure_structured_backend(backend)
    with pytest.raises(contract.RetrievalUnavailableError):
        retrieve.structured_search("revenue")
