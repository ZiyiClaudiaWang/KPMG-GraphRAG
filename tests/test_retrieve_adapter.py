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


def test_search_envelope_answer_preserves_existing_evidence_contract():
    retrieve.configure_graph_backend(_mock_backend(mock_graph_search))
    out = retrieve.search(
        "Intelligent Cloud segment revenue",
        tool="graph",
        trace_id="eval-1",
        request_id="req-1",
    )
    assert out["request_id"] == "req-1"
    assert out["trace_id"] == "eval-1"
    assert out["status"] == "answer"
    assert out["reason"] is None
    assert out["results"]
    assert out["results"][0]["metadata"]["trace_id"] == "eval-1"


def test_search_envelope_empty_results_are_missing_fact_not_backend_failure():
    retrieve.configure_graph_backend(lambda query, filters, top_k, trace_id: [])
    out = retrieve.search(
        "a valid in-scope fact that is not disclosed",
        tool="graph",
        trace_id="E15",
        request_id="req-empty",
    )
    assert out == {
        "request_id": "req-empty",
        "trace_id": "E15",
        "status": "abstain",
        "reason": "missing_fact",
        "results": [],
    }


@pytest.mark.parametrize("reason", ["out_of_scope", "false_premise", "fabricated_relationship"])
def test_search_envelope_can_report_abstention_reasons(reason):
    def backend(query, filters, top_k, trace_id):
        return {"status": "abstain", "reason": reason, "results": []}

    retrieve.configure_graph_backend(backend)
    out = retrieve.search(
        "control question",
        tool="graph",
        trace_id="control-1",
        request_id=f"req-{reason}",
    )
    assert out["status"] == "abstain"
    assert out["reason"] == reason
    assert out["results"] == []


def test_search_envelope_can_report_ambiguous_candidates():
    def backend(query, filters, top_k, trace_id):
        return {"status": "clarify", "reason": "ambiguous", "results": []}

    retrieve.configure_graph_backend(backend)
    out = retrieve.search(
        "J.P. Morgan Securities",
        tool="graph",
        trace_id="tie-1",
        request_id="req-tie",
    )
    assert out["status"] == "clarify"
    assert out["reason"] == "ambiguous"
    assert out["results"] == []


def test_search_envelope_can_report_block():
    def backend(query, filters, top_k, trace_id):
        return {"status": "block", "reason": None, "results": []}

    retrieve.configure_graph_backend(backend)
    out = retrieve.search(
        "DELETE everything",
        tool="graph",
        request_id="req-block",
    )
    assert out["status"] == "block"
    assert out["reason"] is None


def test_search_envelope_backend_failure_stays_unavailable():
    def broken(query, filters, top_k, trace_id):
        raise OSError("database unavailable")

    retrieve.configure_graph_backend(broken)
    with pytest.raises(contract.RetrievalUnavailableError, match="backend failed"):
        retrieve.search("revenue", tool="graph", request_id="req-down")


def test_search_envelope_generates_request_id_for_empty_results():
    retrieve.configure_graph_backend(lambda query, filters, top_k, trace_id: [])
    out = retrieve.search("missing", tool="graph")
    assert isinstance(out["request_id"], str) and out["request_id"]
    assert out["status"] == "abstain"
    assert out["reason"] == "missing_fact"


def test_search_decision_validation_rejects_invalid_combinations():
    with pytest.raises(ValueError, match="answer status"):
        contract.validate_search_decision("answer", "missing_fact")
    with pytest.raises(ValueError, match="clarify status"):
        contract.validate_search_decision("clarify", "missing_fact")
    with pytest.raises(ValueError, match="abstain status"):
        contract.validate_search_decision("abstain", "ambiguous")
