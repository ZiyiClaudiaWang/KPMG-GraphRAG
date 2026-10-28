from __future__ import annotations

import copy

import pytest

from graphrag import contract
from graphrag import retrieve
from mock.mock_retrieval_tools import graph_search as mock_graph_search
from mock.mock_retrieval_tools import structured_search as mock_structured_search


@pytest.fixture(autouse=True)
def clear_backends():
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)
    yield
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)


def _mock_backend(tool):
    """Adapt the INT-0 mock to the real-backend callable shape used by retrieve.py."""
    def backend(query, filters, top_k, trace_id):
        # Wrapper owns trace propagation/ranking; remove them to prove it does.
        rows = copy.deepcopy(tool(query, filters=filters, top_k=top_k, trace_id=None))
        for row in rows:
            row["rank"] = 0
            row["metadata"].pop("trace_id", None)
        return rows
    return backend


def test_unconfigured_graph_backend_is_unavailable_not_no_evidence():
    with pytest.raises(contract.RetrievalUnavailableError, match="not configured"):
        retrieve.graph_search("revenue")


def test_unconfigured_structured_backend_is_unavailable_not_no_evidence():
    with pytest.raises(contract.RetrievalUnavailableError, match="not configured"):
        retrieve.structured_search("revenue")


def test_graph_adapter_produces_contract_evidence():
    retrieve.configure_graph_backend(_mock_backend(mock_graph_search))
    rows = retrieve.graph_search("Intelligent Cloud segment revenue", trace_id="q-17")
    assert rows
    contract.validate_results(rows, top_k=10)
    assert all(row["metadata"]["trace_id"] == "q-17" for row in rows)


def test_structured_adapter_produces_contract_evidence():
    retrieve.configure_structured_backend(_mock_backend(mock_structured_search))
    rows = retrieve.structured_search("revenue", trace_id="q-18")
    assert rows
    contract.validate_results(rows, top_k=10)
    assert all(row["metadata"]["trace_id"] == "q-18" for row in rows)


def test_adapter_applies_shared_input_validation_before_backend():
    called = False

    def backend(query, filters, top_k, trace_id):
        nonlocal called
        called = True
        return []

    retrieve.configure_graph_backend(backend)
    with pytest.raises(ValueError, match="query must be"):
        retrieve.graph_search("")
    assert called is False


def test_adapter_wraps_backend_failure_as_store_unavailable():
    def broken(query, filters, top_k, trace_id):
        raise OSError("database connection dropped")

    retrieve.configure_graph_backend(broken)
    with pytest.raises(contract.RetrievalUnavailableError, match="backend failed"):
        retrieve.graph_search("revenue")


def test_adapter_rejects_malformed_backend_result():
    def malformed(query, filters, top_k, trace_id):
        return [{"score": 1.0}]

    retrieve.configure_graph_backend(malformed)
    with pytest.raises(ValueError, match="missing fields"):
        retrieve.graph_search("revenue")


def test_adapter_honours_top_k_and_reranks():
    def backend(query, filters, top_k, trace_id):
        rows = mock_graph_search("Intelligent Cloud segment revenue", top_k=10)
        rows = list(reversed(copy.deepcopy(rows)))
        for row in rows:
            row["rank"] = 0
        return rows

    retrieve.configure_graph_backend(backend)
    rows = retrieve.graph_search("ignored", top_k=1)
    assert len(rows) == 1
    assert rows[0]["rank"] == 1
