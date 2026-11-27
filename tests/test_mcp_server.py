"""The MCP endpoint hands the arms' own envelope out, and says why when it refuses.

These run against stub arms, so they need no Neo4j and no fact store. What they
pin is the layer, not the retrieval: that nothing is reshaped on the way out,
that the commit is stamped, that a refusal keeps its reason, and that nothing
names the Neo4j URI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graphrag import contract, mcp_server  # noqa: E402

ENVELOPE = [{
    "rank": 1,
    "chunk_id": None,
    "section_id": "sec-1",
    "text": "We face risks from cybersecurity incidents.",
    "quote": "We face risks from cybersecurity incidents.",
    "score": 1.0,
    "section": "Part I Item 1A",
    "ticker": "MSFT",
    "cik": "0000789019",
    "company_name": "MICROSOFT CORP",
    "form": "10-K",
    "accession_number": "0000950170-25-100235",
    "filing_date": "2025-07-30",
    "reporting_period_end": "2025-06-30",
    "source_url": None,
    "chunking_version": None,
    "extraction_version": "claims-v1",
    "index_version": "neo4j-v1",
    # Arm-specific keys the published schema does not name. If declaring a
    # schema trimmed these, the endpoint would hand out a different envelope
    # than the arm returned.
    "metadata": {"trace_id": "t-1", "hops": 3, "section_title": "Risk Factors"},
}]

# The stub stands in for an arm, so what it returns has to be something an arm
# could have returned.
contract.validate_evidence(ENVELOPE[0])


@pytest.fixture
def stub_arms(monkeypatch):
    """Both arms replaced by one that records how it was called."""
    seen = {}

    def fake(name):
        def arm(query, filters=None, top_k=10, trace_id=None):
            seen[name] = {"query": query, "filters": filters,
                          "top_k": top_k, "trace_id": trace_id}
            return ENVELOPE
        return arm

    monkeypatch.setattr(mcp_server.backends, "graph_search", fake("graph"))
    monkeypatch.setattr(mcp_server.backends, "structured_search", fake("structured"))
    return seen


def test_the_envelope_goes_out_untouched(stub_arms):
    out = mcp_server.graph_search("revenue", filters={"ticker": "MSFT"}, top_k=3)
    assert out["results"] == ENVELOPE
    assert out["results"][0] is ENVELOPE[0], "an item was copied or rebuilt"
    assert out["count"] == 1
    assert out["arm"] == "graph"


def test_every_argument_reaches_the_arm(stub_arms):
    mcp_server.structured_search("revenue", filters={"ticker": "MSFT"},
                                 top_k=7, trace_id="eval-q-017")
    assert stub_arms["structured"] == {
        "query": "revenue", "filters": {"ticker": "MSFT"},
        "top_k": 7, "trace_id": "eval-q-017",
    }


def test_every_response_names_the_commit(stub_arms):
    """Eval lags main, and a second agent will too. Three parties disagreeing
    about which version produced a result is the thing this prevents."""
    for out in (mcp_server.graph_search("x"), mcp_server.structured_search("x")):
        assert out["graphrag_commit"], "no commit on the response"


def test_the_commit_is_what_the_environment_says(monkeypatch):
    monkeypatch.setenv("GRAPHRAG_COMMIT", "deadbeef")
    assert mcp_server.commit() == "deadbeef"


def test_nothing_reports_where_an_arm_points(monkeypatch):
    """On Aura the instance id in the URI is also the username, and this
    endpoint is reachable by other teams and logged by whoever runs it."""
    monkeypatch.setitem(mcp_server.backends.status, "graph",
                        "registered: neo4j+s://12345678.databases.neo4j.io")
    monkeypatch.setitem(mcp_server.backends.status, "structured",
                        "registered: /Users/someone/corpus_facts.duckdb")

    reported = mcp_server._redacted_status()
    assert reported["graph"] == "registered"
    assert reported["structured"] == "registered"
    assert "12345678" not in str(reported)
    assert "/Users/" not in str(reported)


def test_a_skip_reason_survives_because_it_names_a_variable_not_a_value(monkeypatch):
    monkeypatch.setitem(mcp_server.backends.status, "cypher",
                        "skipped: CYPHER_ARM_ENABLED is not set to 1")
    assert mcp_server._redacted_status()["cypher"].startswith("skipped: CYPHER_ARM_ENABLED")


# ---- the server itself ----------------------------------------------------

mcp = pytest.importorskip("mcp.server.mcpserver",
                          reason="the MCP SDK is not installed")


@pytest.fixture
def server():
    return mcp_server.build_server()


def test_three_tools_are_published(server, anyio_backend="asyncio"):
    import asyncio
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"graph_search", "structured_search", "describe_filters"}


def test_cypher_is_not_published(server):
    """The seam is on main since #67 and raises until a backend registers.
    A tool that always fails is worse than one that is not there."""
    import asyncio
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert "cypher_search" not in names


def test_the_published_schema_is_the_contract_envelope(server):
    """A tool returning a bare dict publishes {"type": "object"}, which tells
    a consumer nothing. The Agent team asked for the contract first."""
    import asyncio
    tools = {t.name: t for t in asyncio.run(server.list_tools())}

    out = tools["graph_search"].output_schema
    assert sorted(out["properties"]) == ["arm", "count", "graphrag_commit", "results"]

    published = set(out["$defs"]["Evidence"]["properties"])
    assert published == set(contract.EVIDENCE_FIELDS), (
        "the published schema and the envelope have drifted: "
        f"{published ^ set(contract.EVIDENCE_FIELDS)}"
    )


def test_output_validation_does_not_trim_the_envelope(server, stub_arms):
    """metadata keys differ by arm. If declaring a schema dropped the ones it
    does not name, the endpoint would quietly return a different envelope than
    the arm did — which is the whole thing it promises not to do."""
    import asyncio
    result = asyncio.run(server.call_tool("graph_search", {"query": "x"}))
    assert result.structured_content["results"] == ENVELOPE


def test_a_refusal_keeps_its_reason(server, monkeypatch):
    """The SDK masks everything but ToolError to "Error executing tool <name>".

    A refused filter and a graph that is down would read identically, and an
    agent that cannot tell them apart abstains, confidently and wrongly.
    """
    import asyncio
    from mcp.server.mcpserver.exceptions import ToolError

    def refuses(query, filters=None, top_k=10, trace_id=None):
        raise contract.FilterNotSupportedError("the graph arm cannot filter on max_hops")

    monkeypatch.setattr(mcp_server.backends, "graph_search", refuses)

    with pytest.raises(ToolError, match="cannot filter on max_hops"):
        asyncio.run(server.call_tool("graph_search",
                                     {"query": "x", "filters": {"max_hops": 3}}))


def test_a_crash_stays_masked(server, monkeypatch):
    """A driver error can carry the URI. Only anticipated failures explain
    themselves."""
    import asyncio
    from mcp.server.mcpserver.exceptions import UnexpectedToolError

    def crashes(query, filters=None, top_k=10, trace_id=None):
        raise RuntimeError("neo4j+s://12345678.databases.neo4j.io refused the connection")

    monkeypatch.setattr(mcp_server.backends, "graph_search", crashes)

    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(server.call_tool("graph_search", {"query": "x"}))
    assert "12345678" not in str(caught.value)
