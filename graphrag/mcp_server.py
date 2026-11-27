"""Both retrieval arms, over MCP, so an agent can call them without our source tree.

Three tools, each one a thin layer over something that already exists:

    graph_search(query, filters, top_k, trace_id)       -> retrieve.search(tool="graph")
    structured_search(query, filters, top_k, trace_id)  -> retrieve.search(tool="structured")
    describe_filters()                                  -> vocabulary.filters()

Run it:

    python3 -m graphrag.mcp_server                       # stdio, for a local agent
    python3 -m graphrag.mcp_server --transport streamable-http --port 8000
    python3 -m graphrag.mcp_server --mock                # no credentials needed

Four things this module is careful about, each one a defect we already paid for.

**It routes through `retrieve.py`, never a backend module.** That module
owns input validation, trace propagation, ranking and the final conformance
check, and `backends.py` is what registers the real arms into it from the
environment. Calling `graph_backend.search` directly skips all four checks.
The Agent team's own adapter calls past it today, which is the mistake this
exists to stop spreading.

**The envelope is unchanged.** A tool returns the arm's own list of evidence
under `results`, item for item. Nothing is reshaped, renamed or re-sorted on
the way out. What is added sits beside it, never inside it.

**Every response names the commit that served it.** Eval already lags `main` —
round 1 ran on a commit four PRs old — and with a second agent calling the same
endpoint there would otherwise be three parties disagreeing about which version
produced a result. Same key the round 1 result files use, `graphrag_commit`.

**An error is an error, never an empty list.** `[]` means the arm looked and
found nothing. A refused filter, an unconfigured arm or a graph that is down
raises, and the message carries the reason. An agent that reads a failure as
"no evidence exists" abstains confidently and wrongly, which is the worst thing
this endpoint could do quietly.

**The decision travels with the evidence.** `retrieve.search` carries a
`status` and a `reason`; the evidence-list API beside it drops both. An agent
is the one consumer that needs them most, so the tools call the decision API
and pass `status`, `reason` and `request_id` out whole. Non-score abstention
triggers (#86) land on the same seam, so they will reach a caller here with no
change to this file.

`cypher_search` is deliberately absent. The seam is on `main` since #67 but it
raises until something registers a backend, and a tool that always fails is
worse than one that is not there.
"""

from __future__ import annotations

import argparse
import functools
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# pydantic builds the published output schema from these, and below Python
# 3.12 it can only read a TypedDict from typing_extensions — typing's own
# does not keep the annotations it needs. The SDK does not raise when the
# schema cannot be built; it publishes nothing, which is exactly the silent
# hole this module declares types to close. CI runs 3.11, and it caught it.
try:
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover - 3.12+ without typing_extensions
    from typing import TypedDict

from . import backends, contract, retrieve, vocabulary

_ROOT = Path(__file__).resolve().parent.parent

SERVER_NAME = "kpmg-graphrag"

INSTRUCTIONS = """Retrieval over SEC filings, in two arms.

`graph_search` searches claims extracted from filing prose and returns the
sentence a filer wrote, with the filing and section it came from. Ask it what a
company said.

`structured_search` searches an XBRL fact table and returns a tagged number
with its concept, period and dimensions. Ask it what a company reported.

Call `describe_filters` before sending filters. An arm applies, ignores or
refuses each key, and only the first does what you expect.

Both return evidence, not answers. Compose the answer yourself, and cite the
provenance key each item carries: `section_id` for the graph arm,
`metadata.fact_id` for the structured arm. Exactly one is populated."""


def commit() -> str:
    """The commit this server is serving, for the response stamp.

    A deployment without a git directory sets GRAPHRAG_COMMIT instead. Neither
    available is reported as unknown rather than guessed at — a wrong commit is
    worse than none, because it would be believed.
    """
    stamped = os.environ.get("GRAPHRAG_COMMIT")
    if stamped:
        return stamped
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 else "unknown"


class Evidence(TypedDict):
    """One item of the contract envelope, exactly as an arm returns it.

    Declared so the endpoint publishes a real output schema. A tool that
    returns a bare dict publishes `{"type": "object"}`, which tells a consumer
    nothing — and the first thing the Agent team asked for was the contract.

    `contract.EVIDENCE_FIELDS` is the source of truth and a test holds this to
    it field for field, so the published schema cannot drift from the envelope
    it describes.

    `metadata` stays a free-form mapping on purpose: its keys differ by arm
    (`fact_id`, `concept` and `dimensions` on the structured arm, `hops` and
    `section_title` on the graph arm), and pinning them here would either go
    stale or quietly drop the ones not listed.
    """

    rank: int
    chunk_id: str | None
    section_id: str | None
    text: str
    quote: str | None
    score: float
    section: str | None
    ticker: str | None
    cik: str
    company_name: str
    form: str
    accession_number: str
    filing_date: str
    reporting_period_end: str | None
    source_url: str | None
    chunking_version: str | None
    extraction_version: str
    index_version: str
    metadata: dict[str, Any]


class SearchResponse(TypedDict):
    """What a search tool returns: the decision, the envelope, and the stamp.

    `status` is `answer`, `abstain` or `clarify`, and `reason` says which kind
    when it is not an answer — `missing_fact`, `out_of_scope`, `false_premise`,
    `fabricated_relationship`, or `ambiguous` for a clarify. An answer always
    carries evidence and never a reason; everything else carries a reason and
    no evidence.

    Empty `results` on its own does not say whether the arm looked and found
    nothing or declined to look. The decision does, and an agent deciding
    whether to answer needs it more than it needs the rows.
    """

    request_id: str
    status: str
    reason: str | None
    results: list[Evidence]
    count: int
    arm: str
    graphrag_commit: str


class FiltersResponse(TypedDict):
    """What `describe_filters` returns."""

    filters: dict[str, dict[str, list[str]]]
    arms: dict[str, str]
    graphrag_commit: str


def _redacted_status() -> dict[str, str]:
    """Which arms are configured, without saying what they are configured with.

    `backends.status` names the Neo4j URI and the store path, and both are
    sensitive in different ways. On Aura the instance id in the hostname is
    also the username, and this endpoint is reachable by other teams and
    logged by whoever runs it. A caller needs to know an arm is up, never
    where it points.

    A skip reason names the environment variable that is missing, not its
    value, so those travel whole. The one exception is the missing-store
    message, which quotes the path it looked at.
    """
    safe = {}
    for arm, state in backends.status.items():
        if state.startswith("registered"):
            safe[arm] = "registered"
        elif state.startswith("skipped: no DuckDB store"):
            safe[arm] = "skipped: no DuckDB store at the configured path"
        else:
            safe[arm] = state
    return safe


def _search(arm: str, query, filters, top_k, trace_id) -> SearchResponse:
    """One arm, through the decision API, with the stamp beside its answer.

    `retrieve.search` is the layer that carries the decision. The evidence-list
    API next to it drops `status` and `reason` on the floor — literally
    `_, _, results = _run(...)` — which is right for a caller that only wants
    rows and wrong for an agent deciding whether to answer at all.
    """
    envelope = retrieve.search(query, tool=arm, filters=filters,
                               top_k=top_k, trace_id=trace_id)
    return {
        "request_id": envelope["request_id"],
        "status": envelope["status"],
        "reason": envelope["reason"],
        "results": envelope["results"],
        "count": len(envelope["results"]),
        "arm": arm,
        "graphrag_commit": commit(),
    }


def graph_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> SearchResponse:
    """Search claims extracted from filing prose.

    Returns the sentence a filer wrote, with `section_id` naming where it came
    from. Use it for what a company said: risks, commitments, descriptions.

    filters: see `describe_filters`. The arm refuses a key it cannot serve
    rather than ignoring it, so a refusal is information, not a bug.
    """
    return _search("graph", query, filters, top_k, trace_id)


def structured_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> SearchResponse:
    """Search the XBRL fact table.

    Returns a tagged number with its concept, period and dimensions, and
    `metadata.fact_id` naming the fact. Use it for what a company reported.

    A fact with no dimensions is the consolidated figure; one with dimensions
    is a slice of it. Summing both double-counts.
    """
    return _search("structured", query, filters, top_k, trace_id)


def describe_filters() -> FiltersResponse:
    """What each arm does with every filter key it is offered.

    `applied` reaches the query and narrows the result. `ignored` is accepted
    and never read. `refused` raises by name. Read this before sending filters:
    an ignored key returns unfiltered rows that look like filtered ones.

    `arms` reports which arms this server has configured, and names the reason
    for any it could not.
    """
    return {
        "filters": vocabulary.filters(),
        "arms": _redacted_status(),
        "graphrag_commit": commit(),
    }


TOOLS = (graph_search, structured_search, describe_filters)


# What the arms raise on purpose, and a caller can act on: a filter this arm
# cannot serve, an arm that is not configured, a query or top_k outside the
# contract. Anything else is a crash.
ANTICIPATED = (
    contract.FilterNotSupportedError,
    contract.RetrievalUnavailableError,
    ValueError,
)


def _carries_its_reason(fn):
    """Let a deliberate refusal keep its text on the way to the caller.

    The SDK masks every exception but `ToolError` to "Error executing tool
    <name>" with nothing after it, so a refused filter and a graph that is
    down read identically. That is the one thing this endpoint must not do:
    an agent that cannot tell a refusal from an outage treats both as no
    evidence and abstains, confidently and wrongly.

    A crash stays masked, deliberately. A driver error can carry the URI, and
    the instance id in it is also the username on Aura.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except contract.RetrievalUnavailableError as exc:
            # An outage is worth saying out loud — a caller must not read it as
            # an absence of evidence — but retrieve.py folds the backend's own
            # message into this one, and a driver error carries the URI. Say
            # that the arm is down, keep what the driver said in the log.
            print(f"retrieval unavailable: {exc}", file=sys.stderr, flush=True)
            raise ToolError(_without_the_driver_text(str(exc))) from exc
        except ANTICIPATED as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


_FAILED = "retrieval backend failed"


def _without_the_driver_text(message: str) -> str:
    """Keep the arm and the fact it is down, drop what the driver said.

    "graph retrieval backend is not configured" is the whole message and is
    safe. "graph retrieval backend failed: <driver error>" is not — the error
    can name the host, and the instance id in it is the username on Aura.
    """
    if _FAILED not in message:
        return message
    arm = message.split(_FAILED, 1)[0].strip()
    return (
        f"{arm} {_FAILED}. This is an outage, not an absence of evidence — do "
        "not answer as though nothing was found. The underlying error is in "
        "the server log."
    )


def _configure_mock() -> dict[str, str]:
    """Serve the mock arms instead of the real ones.

    So another team can integrate today. They get the same three tools, the
    same schemas and the same envelope, against three fixed Microsoft rows,
    with no credentials and no graph to reach. Since #103 the mock's shape
    matches the real arm field for field, so code written against this does
    not change when the credentials arrive.

    It is registered at the same seam the real arms use, so the call still
    goes through retrieve.py — the mock stands in for a backend, not for the
    contract.
    """
    sys.path.insert(0, str(_ROOT / "mock"))
    import mock_retrieval_tools as mock

    retrieve.configure_graph_backend(mock.graph_search)
    retrieve.configure_structured_backend(mock.structured_search)
    backends.status.clear()
    backends.status.update({
        "graph": "registered: mock",
        "structured": "registered: mock",
        "cypher": "skipped: not served by the mock",
    })
    return {"graph": "registered: mock arm, three fixed rows",
            "structured": "registered: mock arm, three fixed rows",
            "cypher": "skipped: not served by the mock"}


def build_server():
    """The MCP server, with the three tools registered.

    Imported lazily so the module above can be tested, and the arms called,
    without the MCP SDK installed.
    """
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name=SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=commit()[:12],
    )
    for tool in TOOLS:
        server.tool()(_carries_its_reason(tool))
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the retrieval arms over MCP.")
    parser.add_argument("--transport", default="stdio",
                        choices=("stdio", "sse", "streamable-http"))
    parser.add_argument("--port", type=int, default=8000,
                        help="HTTP transports only; ignored on stdio")
    parser.add_argument("--mock", action="store_true",
                        help="serve the mock arms, so a consumer can integrate "
                             "against the same tools and schemas with no "
                             "credentials and no graph")
    args = parser.parse_args(argv)

    # Register whatever the environment describes, and say so before serving.
    # An arm that is missing its credentials stays unregistered and raises on
    # every call, which is worth knowing at startup rather than mid-question.
    #
    # Redacted here too: this line lands in whatever log the host keeps, and
    # the raw status names the Neo4j URI, whose instance id is also the
    # username on Aura.
    # To stderr, never stdout. On the stdio transport stdout *is* the JSON-RPC
    # channel, and a plain line on it reaches the client as a malformed
    # message — three parse errors in its log before the handshake even
    # finishes. Every client recovers; a stricter one would not have to.
    if args.mock:
        for arm, state in _configure_mock().items():
            print(f"{arm}: {state}", file=sys.stderr, flush=True)
    else:
        backends.configure_from_env()
        for arm, state in _redacted_status().items():
            print(f"{arm}: {state}", file=sys.stderr, flush=True)

    server = build_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
