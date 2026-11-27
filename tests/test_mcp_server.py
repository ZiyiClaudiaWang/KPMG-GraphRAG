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

from graphrag import contract, mcp_server, retrieve  # noqa: E402

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
    "metadata": {"trace_id": "t-1", "hops": 3, "section_title": "Risk Factors",
                 "retrieval_method": "stub"},
}]

# The stub stands in for an arm, so what it returns has to be something an arm
# could have returned.
contract.validate_evidence(ENVELOPE[0])


@pytest.fixture
def stub_arms(monkeypatch):
    """Stub backends registered where the real ones register.

    At the backend seam, not over the tool functions, so every call still goes
    through retrieve.py's validation, decision and conformance check — which is
    the layer this endpoint exists to stay behind.
    """
    seen = {}

    def fake(name, returns=ENVELOPE):
        def arm(query, filters=None, top_k=10, trace_id=None):
            seen[name] = {"query": query, "filters": filters,
                          "top_k": top_k, "trace_id": trace_id}
            return returns
        return arm

    retrieve.configure_graph_backend(fake("graph"))
    retrieve.configure_structured_backend(fake("structured"))
    yield seen
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)


@pytest.fixture
def arm_returning():
    """Register one graph backend that returns whatever a test hands it."""
    def register(value):
        retrieve.configure_graph_backend(lambda q, f=None, k=10, t=None: value)
    yield register
    retrieve.configure_graph_backend(None)


def test_the_envelope_goes_out_untouched(stub_arms):
    # trace_id is sent as the stub's own, because retrieve.py stamps every row
    # with the call's trace_id. That is its job, and the only thing between the
    # arm and the caller that touches a row.
    out = mcp_server.graph_search("revenue", filters={"ticker": "MSFT"},
                                  top_k=3, trace_id="t-1")
    assert out["results"] == ENVELOPE, "a field was reshaped, renamed or dropped"
    assert out["count"] == 1
    assert out["arm"] == "graph"


def test_every_argument_reaches_the_arm(stub_arms):
    mcp_server.structured_search("revenue", filters={"ticker": "MSFT"},
                                 top_k=7, trace_id="eval-q-017")
    assert stub_arms["structured"] == {
        "query": "revenue", "filters": {"ticker": "MSFT"},
        "top_k": 7, "trace_id": "eval-q-017",
    }


def test_the_decision_travels_with_the_evidence(stub_arms):
    """Empty results alone cannot say whether the arm looked and found nothing
    or declined to look. An agent deciding whether to answer needs the
    difference more than it needs the rows."""
    out = mcp_server.graph_search("revenue")
    assert out["status"] == "answer"
    assert out["reason"] is None
    assert out["request_id"]


def test_an_arm_with_nothing_to_say_abstains_with_a_reason(arm_returning):
    arm_returning([])
    out = mcp_server.graph_search("who will win next year")
    assert out["status"] == "abstain"
    assert out["reason"] == "missing_fact"
    assert out["results"] == []


def test_a_richer_abstention_passes_through_untouched(arm_returning):
    """An arm that knows why it is declining says so, and #86's non-score
    triggers land on this seam — so they reach a caller here with no change
    to the server."""
    arm_returning({"status": "abstain", "reason": "out_of_scope", "results": []})
    out = mcp_server.graph_search("what is the weather")
    assert (out["status"], out["reason"]) == ("abstain", "out_of_scope")


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


def test_the_published_tools_are_the_ones_we_mean(server):
    import asyncio
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"graph_search", "structured_search",
                     "describe_filters", "describe_corpus"}


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
    # The SDK publishes nothing rather than raising when it cannot read the
    # return type. Below Python 3.12 that happens with typing's own TypedDict,
    # and the endpoint would look fine while telling consumers nothing.
    assert out is not None, "no output schema was published for graph_search"
    assert sorted(out["properties"]) == [
        "arm", "count", "graphrag_commit", "reason", "request_id", "results", "status",
    ]

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
    result = asyncio.run(server.call_tool("graph_search", {"query": "x", "trace_id": "t-1"}))
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

    retrieve.configure_graph_backend(refuses)
    try:
        with pytest.raises(ToolError, match="cannot filter on max_hops"):
            asyncio.run(server.call_tool("graph_search", {"query": "x"}))
    finally:
        retrieve.configure_graph_backend(None)


def test_an_outage_says_so_without_saying_where(server, monkeypatch):
    """An outage has to be loud — a caller must not read it as no evidence —
    but retrieve.py folds the driver's own message into the error it raises,
    and a driver error carries the URI."""
    import asyncio
    from mcp.server.mcpserver.exceptions import ToolError

    def crashes(query, filters=None, top_k=10, trace_id=None):
        raise RuntimeError("neo4j+s://12345678.databases.neo4j.io refused the connection")

    retrieve.configure_graph_backend(crashes)
    try:
        with pytest.raises(ToolError) as caught:
            asyncio.run(server.call_tool("graph_search", {"query": "x"}))
        said = str(caught.value)
        assert "12345678" not in said, "the endpoint named the host"
        assert "neo4j" not in said.lower(), "the endpoint named the driver"
        assert "not an absence of evidence" in said
    finally:
        retrieve.configure_graph_backend(None)


def test_the_mock_arms_serve_the_same_tools(server):
    """Another team can integrate before the credentials question is settled.

    Same three tools, same schemas, same envelope, three fixed Microsoft rows.
    It registers at the backend seam, so the call still goes through
    retrieve.py — the mock stands in for a backend, not for the contract.
    """
    import asyncio
    mcp_server._configure_mock()
    try:
        result = asyncio.run(server.call_tool(
            "structured_search", {"query": "Intelligent Cloud revenue"}))
        body = result.structured_content
        assert body["status"] == "answer"
        assert body["count"] > 0
        contract.validate_evidence(body["results"][0])
        assert body["results"][0]["metadata"]["fact_id"]
    finally:
        retrieve.configure_graph_backend(None)
        retrieve.configure_structured_backend(None)


# ---- describe_corpus -------------------------------------------------------

def test_the_corpus_tool_is_published(server):
    import asyncio
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert "describe_corpus" in names


def test_an_unreachable_arm_says_so_rather_than_holding_nothing(monkeypatch):
    """An empty filer list reads as "we do not hold that company". An arm
    that could not be asked has to say that instead."""
    from graphrag import corpus as corpus_module

    def fails():
        raise RuntimeError("no store configured")

    monkeypatch.setattr(corpus_module, "_structured_corpus", fails)
    monkeypatch.setattr(corpus_module, "_graph_corpus",
                        lambda: {"filers": [], "tickers": [], "filings": 0})

    report = corpus_module.corpus()
    assert "unavailable" in report["structured"]
    assert "no store configured" in report["structured"]["unavailable"]
    assert "unavailable" not in report["graph"]


def test_the_fiscal_years_come_from_the_arms_own_labels():
    """They are what that arm accepts as fiscal_year, not a calendar range."""
    from graphrag import corpus as corpus_module

    calendar = {"MSFT": {"FY 2026": (None, None), "Q3 2026": (None, None),
                         "Q1 2027": (None, None)}}
    assert corpus_module._years(calendar, "MSFT") == [2026, 2027]
    assert corpus_module._years(calendar, "AAPL") == []
