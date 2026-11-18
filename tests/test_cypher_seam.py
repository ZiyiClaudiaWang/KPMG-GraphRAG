from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Every module here puts the repo root on the path itself. Collection is
# alphabetical and nothing guarantees another file has already done it.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graphrag import backends, contract, retrieve  # noqa: E402


@pytest.fixture(autouse=True)
def clear_backends():
    retrieve.configure_graph_backend(None)
    retrieve.configure_cypher_backend(None)
    yield
    retrieve.configure_graph_backend(None)
    retrieve.configure_cypher_backend(None)


def _row(method: str) -> dict:
    return {
        "company_name": "MICROSOFT CORP",
        "ticker": "MSFT",
        "cik": "0000789019",
        "accession_number": "0001193125-26-323660",
        "form": "10-K",
        "filing_date": "2026-07-29",
        "reporting_period_end": "2026-06-30",
        "source_url": None,
        "section": None,
        "chunking_version": None,
        "extraction_version": "test",
        "index_version": "test",
        "text": "a claim",
        "quote": None,
        "score": 1.0,
        "section_id": "sec-1",
        "chunk_id": None,
        "metadata": {"retrieval_method": method},
    }


def test_cypher_search_raises_until_something_registers():
    """An unregistered arm is an outage, not an empty result."""
    with pytest.raises(contract.RetrievalUnavailableError):
        retrieve.cypher_search("which companies share a regulatory risk")


def test_registering_cypher_leaves_the_graph_arm_alone():
    """The whole point of the split: scoring one arm never reaches the other."""
    retrieve.configure_cypher_backend(lambda q, f, k, t: [_row("neo4j_text2cypher")])

    assert retrieve.cypher_search("anything")[0]["metadata"]["retrieval_method"] == (
        "neo4j_text2cypher"
    )
    with pytest.raises(contract.RetrievalUnavailableError):
        retrieve.graph_search("anything")


def test_each_arm_returns_its_own_rows():
    retrieve.configure_graph_backend(lambda q, f, k, t: [_row("neo4j_fulltext_claim")])
    retrieve.configure_cypher_backend(lambda q, f, k, t: [_row("neo4j_text2cypher")])

    graph = retrieve.graph_search("anything")[0]["metadata"]["retrieval_method"]
    cypher = retrieve.cypher_search("anything")[0]["metadata"]["retrieval_method"]
    assert (graph, cypher) == ("neo4j_fulltext_claim", "neo4j_text2cypher")


def test_the_wrapper_still_owns_trace_and_rank():
    """The new arm inherits validation and ranking rather than redoing them."""
    retrieve.configure_cypher_backend(
        lambda q, f, k, t: [_row("neo4j_text2cypher"), _row("neo4j_text2cypher")]
    )
    rows = retrieve.cypher_search("anything", trace_id="t-1", top_k=2)

    assert [r["rank"] for r in rows] == [1, 2]
    assert all(r["metadata"]["trace_id"] == "t-1" for r in rows)


def test_cypher_takes_the_same_filter_keys_as_the_graph_arm():
    assert contract.CYPHER_FILTERS == contract.GRAPH_FILTERS

    retrieve.configure_cypher_backend(lambda q, f, k, t: [])
    retrieve.cypher_search("anything", filters={"ticker": "MSFT", "max_hops": 3})
    with pytest.raises(ValueError):
        retrieve.cypher_search("anything", filters={"nonsense": 1})


def test_a_non_callable_backend_is_refused():
    with pytest.raises(TypeError):
        retrieve.configure_cypher_backend("not callable")


def test_env_without_the_module_skips_and_says_so(monkeypatch):
    """Until `cypher_backend.py` exists, the other two arms carry on."""
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example")
    monkeypatch.setenv("CYPHER_ARM_ENABLED", "1")
    # None makes the import fail whether or not the file exists on disk, the
    # same way test_backends.py handles graph_backend.
    monkeypatch.setitem(sys.modules, "graphrag.cypher_backend", None)

    reason = backends._register_cypher()
    assert reason.startswith("skipped:")
    assert "cypher_backend" in reason


def test_env_with_the_module_registers_it(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example")
    monkeypatch.setenv("CYPHER_ARM_ENABLED", "1")
    stub = types.ModuleType("graphrag.cypher_backend")
    stub.search = lambda q, f, k, t: [_row("neo4j_text2cypher")]
    monkeypatch.setitem(sys.modules, "graphrag.cypher_backend", stub)

    assert backends._register_cypher() == "registered: neo4j+s://example"
    assert retrieve.cypher_search("anything")[0]["score"] == 1.0


def test_no_uri_skips_without_importing_anything(monkeypatch):
    monkeypatch.setenv("CYPHER_ARM_ENABLED", "1")
    monkeypatch.delenv("NEO4J_URI", raising=False)
    assert backends._register_cypher() == "skipped: NEO4J_URI is not set"


def test_off_by_default_even_with_the_module_and_uri(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example")
    monkeypatch.delenv("CYPHER_ARM_ENABLED", raising=False)
    stub = types.ModuleType("graphrag.cypher_backend")
    stub.search = lambda q, f, k, t: [_row("neo4j_text2cypher")]
    monkeypatch.setitem(sys.modules, "graphrag.cypher_backend", stub)

    assert backends._register_cypher().startswith("skipped: CYPHER_ARM_ENABLED")
    with pytest.raises(contract.RetrievalUnavailableError):
        retrieve.cypher_search("anything")


def test_a_row_labelled_as_another_arm_is_rejected():
    retrieve.configure_cypher_backend(lambda q, f, k, t: [_row("neo4j_fulltext_claim")])
    with pytest.raises(ValueError, match="retrieval_method"):
        retrieve.cypher_search("anything")


def test_a_bad_generated_query_is_not_an_outage():
    def backend(q, f, k, t):
        raise contract.QueryGenerationError("Invalid input 'MATC'")
    retrieve.configure_cypher_backend(backend)
    with pytest.raises(contract.QueryGenerationError):
        retrieve.cypher_search("anything")


def test_a_row_with_no_retrieval_method_is_rejected():
    row = _row("neo4j_text2cypher")
    row["metadata"] = {}
    retrieve.configure_cypher_backend(lambda q, f, k, t: [row])
    with pytest.raises(ValueError, match="retrieval_method"):
        retrieve.cypher_search("anything")


def test_the_tool_is_known_even_while_the_arm_is_off(monkeypatch):
    """Eval can ask for the cypher tool before the adapter exists.

    The envelope's statuses describe the question, not whether an arm is
    running, so an arm that is off must not come back as `abstain` — that
    would tell Eval the question was out of scope. It reports unavailable,
    the same as the other two arms do when nothing has registered them, and
    `backends.status` carries the reason.
    """
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example")
    monkeypatch.delenv("CYPHER_ARM_ENABLED", raising=False)
    backends.configure_from_env()

    assert backends.status["cypher"].startswith("skipped: CYPHER_ARM_ENABLED")
    with pytest.raises(contract.RetrievalUnavailableError):
        retrieve.search("anything", tool="cypher")
