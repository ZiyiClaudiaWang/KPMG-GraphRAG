# The MCP endpoint

Both retrieval arms as MCP tools, so an agent can call them without importing us.

`graphrag/mcp_server.py`. Three tools, each a thin layer over something that already exists — no new retrieval, no new ranking, no reshaping of what an arm returns.

## Running it

```bash
pip install -r requirements-dev.txt          # the MCP SDK is in there now

python3 -m graphrag.mcp_server               # stdio, for an agent on the same machine
python3 -m graphrag.mcp_server --transport streamable-http --port 8000
```

It prints which arms it registered before it starts serving, so a missing credential is something you see at startup rather than mid-question:

```
graph: registered
structured: registered
cypher: skipped: CYPHER_ARM_ENABLED is not set to 1
```

Environment, same as `backends.py` reads today:

| arm | needs |
| --- | --- |
| graph | `NEO4J_URI`, `NEO4J_USERNAME` (on Aura, the instance id — not `neo4j`), `NEO4J_PASSWORD`, optionally `NEO4J_DATABASE` |
| structured | `MSFT_FACT_DB_PATH`, pointing at a DuckDB store a loader wrote |

In an MCP client's config:

```json
{
  "mcpServers": {
    "kpmg-graphrag": {
      "command": "python3",
      "args": ["-m", "graphrag.mcp_server"],
      "cwd": "/path/to/KPMG-GraphRAG",
      "env": { "NEO4J_URI": "...", "MSFT_FACT_DB_PATH": "..." }
    }
  }
}
```

## The tools

### `graph_search(query, filters?, top_k?, trace_id?)`

Claims extracted from filing prose. Returns the sentence a filer wrote, with `section_id` naming where it came from. Ask it **what a company said** — risks, commitments, descriptions.

### `structured_search(query, filters?, top_k?, trace_id?)`

The XBRL fact table. Returns a tagged number with its concept, period and dimensions, and `metadata.fact_id` naming the fact. Ask it **what a company reported**.

A fact with no dimensions is the consolidated figure; one with dimensions is a slice of it. Summing both double-counts.

### `describe_filters()`

What each arm does with every filter key it is offered — `applied`, `ignored` or `refused` — read from the arms themselves, so it cannot disagree with them. **Call this before sending filters.** An ignored key returns unfiltered rows that look exactly like filtered ones.

Also reports which arms this server has configured, and the reason for any it could not.

## What comes back

The arm's own envelope, item for item, under `results`. Nothing is renamed, reshaped or re-sorted. What this layer adds sits beside it:

```json
{
  "results": [ ... the contract envelope, unchanged ... ],
  "count": 5,
  "arm": "graph",
  "graphrag_commit": "2af8050..."
}
```

`graphrag_commit` is the commit that served the call — same key the round 1 result files use. Eval already lags `main`, and with a second agent on the same endpoint there would otherwise be three parties disagreeing about which version produced a result. Quote it with any number you report.

## Errors

**An empty `results` means the arm looked and found nothing. Anything else raises.** A refused filter, an unconfigured arm, a query or `top_k` outside the contract — all of them come back as a `ToolError` whose text is the reason:

```
Error executing tool graph_search: the graph arm cannot filter on max_hops:
this arm walks exactly 3 hops, Claim to Section to Filing to Company, and they
run upward to fill the evidence envelope rather than outward to find anything...
```

Do not treat a failure as "no evidence exists". An agent that abstains on an error abstains confidently and wrongly, and nothing in the transcript says why.

A crash is masked to `Error executing tool <name>` with nothing after it, on purpose: a driver error can carry the Neo4j URI, and on Aura the instance id in it is also the username.

## What is not here, and why

**`cypher_search`.** The seam is on `main` since #67, but it raises until something registers a backend. A tool that always fails is worse than one that is not there. It goes in when `graphrag/cypher_backend.py` exists.

**The agent as a tool.** The decision that the GraphRAG team owns its own agent is recorded (#108), and the agent itself is not built. When it is, it joins this server as a fourth tool: the arms stay exposed for Eval, who score them directly, and the agent is there for anyone who wants an answer rather than evidence.

**Hosting.** Running this still needs the repo and the credentials, so "without our source tree" is only half true today — it removes the Python import, not the deployment. Who hosts it, and whose credentials it runs on, is an open decision and the one thing between this and another team using it for real. ⚠️ The Aura credential can write and this tier has no read-only user, so a hosted endpoint on it is a decision about write access, not just uptime.
