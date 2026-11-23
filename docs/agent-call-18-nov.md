# Agent team call — 18 November 2026

Updated 23 November against `yz5405/kpmg-agentic-backbone` `main` at `9fcd5b9`
(PR #14 v0.4 and PR #15 merged 22 November). PRs #9, #11, #13 and
`feat/real-rag-retrieval` are still open. 

# Content
---
**Urgent**
1. The fallback mode still drops good evidence
2. A quarter without a fiscal year ends in ERROR
3. Run one real answer end to end

**Decision/Discussion Needed**

4. Keep one workflow
5. Who owns the arithmetic

**FYI**

6. The import
7. Every row says which arm produced it
8. Four failures, one of which you should never retry
9. What "no evidence" looks like, and who says "I don't know"
10. You cannot traverse the graph through anything we have given you
11. Two things that look like bugs and are not

**The bigger picture: what the Agent layer still has to deliver**


# Urgent

## 1 · The fallback mode still drops good evidence

**Done 22 Nov:** `tool_selection.py` chooses a tool before retrieval
(`KPMG_TOOL_SELECTION=auto`), calls `graph_search`, allows one alternate arm,
and runs the financial evaluator only on financial questions. Your 22 Nov
smoke test routed a Microsoft competition-claims question to Graph with zero
fallbacks.

**The bug, still in fallback mode.** With `KPMG_GRAPHRAG_ENABLED=1` and no
`KPMG_TOOL_SELECTION=auto`, `select_outcome` still sends every non-error
question through `evaluate_financial_evidence`. It returns `[]` for anything
that is not one of its regex shapes, asks for the GraphRAG fallback, and ends
in `NO_EVIDENCE`, so a narrative question that RAG answered is thrown away.
Delete the fallback mode (§4), or run the evaluator only on the fallback path
and keep RAG's evidence when it declines.

**Our rows and the evaluator.** On financial questions,
`evaluate_financial_evidence` requires `metadata.fiscal_year` and
`fiscal_period` to equal the parsed filters, and a text line like
`Total revenue 70,066`. Our structured rows carry
`us-gaap:RevenueFromContract…: 70066000000 USD` with `period_start` /
`period_end`, so outside the MSFT Q3 revenue special case a correct row can be
rejected. Match on `concept`, `period_start`, `period_end` and `value_num`, as
your annual path already does. `_matches_filters` already skips missing fiscal
labels for our arms; do the same in the evaluator.

**Route subsidiary questions to Graph.** Graph is chosen today only for
"claims / disclosures about …". "Which subsidiaries does Apple have in
Ireland?" goes to Vector. Add:

```python
if re.search(r"\b(subsidiar(y|ies)|owns|acquired)\b", query, re.I):
    return "graph", "narrative"
```

Until `neighbours` exists (§10), Graph means `graph_search`, which returns
sentences, not edges.

**Route by what the arms do today**, not by Layer 3 of
`schema-ids-operations.md`:

| Type | Layer 3 says | Route to today |
|---|---|---|
| Relational / multi-hop | Graph | Abstain (§10) |
| Subsidiary | Graph | `graph_search` (text match; `MENTIONS` links are loaded) |
| Global synthesis | Community summaries | Abstain; never built |

**State.** `AgentGraphState` keeps RAG and GraphRAG evidence separate, but
there is still one pass and at most one alternate arm, so the multi-input
questions in §5 still need an accumulator.

**Decision: (a) delete or fix the fallback mode; (b) relax the evaluator to
match on periods; (c) add the subsidiary route; (d) measure the share of
questions routed to the right arm.**


## 2 · A quarter without a fiscal year ends in ERROR

**Done 22 Nov:** the ticker map has the ten companies, and a bare year is no
longer read as `fiscal_year`.

Still open:

- **`fiscal_period` without `fiscal_year`.** "What was Nike's revenue in Q3?"
  parses to `fiscal_period="Q3"` and no year. Our arms refuse that filter, your
  loop stops on `unsupported_filter`, and the user gets `ERROR`. Either ask
  which fiscal year, or drop the period and say so. Do not send it alone.
- **The `MSFT + "March 31, 2026" → Q3 FY2026` special case** in
  `query_parser.py` is still there. Period filters work on both arms now, so
  drop it.
- **Lists work.** Our arms take a list for `ticker` and `fiscal_year` (applied
  as an `IN`). For multi-company or multi-period questions, one call to our
  arms is enough; `retrieve_multi` is only needed for RAG.
- **`form` filter.** You now send `form`. Both arms accept it; check that the
  values you send are `10-K` / `10-Q`.
- **Coverage (PR #13, open).** Our coverage is the ten tickers, 2021–2026,
  233 filings, which is not RAG's list. Hard-code ours in the precheck, so a
  question RAG cannot cover can still reach us.
- **Valid filter values.** Do not call `graphrag.vocabulary`; it is not on our
  `main`. Use the rules above and your own ticker map.

**Decision: never send a quarter without a fiscal year.**


## 3 · Run one real answer end to end

The answer node and the LLM client are built, but every v0.4 check ran with
both LLM flags off: `KPMG_QUERY_LLM_ENABLED` and `KPMG_ANSWER_ENABLED` are off
by default, and your own docs list "a real model-generated answer with
citations in Financial Explorer" as the remaining check.

The online demo (Render) uses bundled sample data, with no live retrieval and
no generated answers. It is not an end-to-end run.

Run once, before the 30 November freeze: auto mode, real Chroma, both of our
arms, LLM query understanding and answer generation on, in Financial Explorer.
Keep the raw trace; the v0.4 regression, Neo4j and UI results were not kept.

Then run the gold set through the agent, with the share of questions routed
correctly as the first number (INT-3).

**Decision: who runs it, and by when.**


# Decision/Discussion Needed

## 4 · Keep one workflow

The API now has two GraphRAG modes, and the orchestrator still lives in
`demo/github-demo.zip`.

| | Auto (`KPMG_TOOL_SELECTION=auto`) | Fallback (`KPMG_GRAPHRAG_ENABLED=1` only) |
|---|---|---|
| Tool choice | Vector / Structured / Graph by rule | RAG, then one GraphRAG arm on weak |
| Evaluator | Financial questions only | Every question (§1) |
| Abstain reason | Yes, for calculations and relationships | No |
| Errors | Classified | Bare `Exception` (§8) |

**Decision: keep auto, delete fallback, and delete the demo orchestrator or
move its clarify / off-topic step into auto.**


## 5 · Who owns the arithmetic

Eval compared RAG and our structured arm on one Microsoft 10-Q
(`0001193125-26-191507`), `top_k=8`. Of 25 numeric misses, 13 ranked below 8,
10 needed several inputs, 2 were not in the top 50.

**The ten multi-input ones still have no owner.** v0.4 abstains on them
(`unsupported_calculation`): rankings, `calculate`, `percentage change`, and
numeric comparisons. That is a safe default, not a decision. Layer 2 lists
`compare_periods`, `ratio` and `trend` as structured operations; the arm
returns facts only, and nothing in your workflow composes two facts.

**Decision: pick one. If ours, we build the three operations. If yours, we
delete those rows from Layer 2. If neither before 30 Nov, say so and the
readout lists them as out of scope.**


# FYI

## 6 · The import

**Done 22 Nov** in `agent/tools/graphrag_tool.py`: it now imports
`structured_search` and `graph_search` from `graphrag.backends`.

Still open, only if you keep the demo orchestrator (§4):
`demo/03_GraphRAG_graphrag_adapter.py` still imports `graphrag.retrieve`, and
`demo/github-demo.zip` still bundles its own 10 November copy of our
`graphrag/contract.py`. Import ours rather than vendoring it.

Setup:

- We have no `pyproject.toml` yet. Put our checkout on `PYTHONPATH`, as your
  `docs/v04-agent-setup.md` already says.
- Graph needs `NEO4J_URI`, `NEO4J_USERNAME` (or `NEO4J_USER`),
  `NEO4J_PASSWORD`, `NEO4J_DATABASE`. Structured needs `MSFT_FACT_DB_PATH`;
  point it at the 233-filing store for the ten companies, not the MSFT-only
  default.
- Log `graphrag.backends.status` at startup. It names each arm and why any one
  did not start.

**Decision: fix the demo adapter or delete it.**


## 7 · Every row says which arm produced it

| Arm | `metadata.retrieval_method` |
|---|---|
| `graph_search` | `neo4j_fulltext_claim` |
| `structured_search` | `duckdb_concept_match` |
| `cypher_search` | `neo4j_text2cypher` |

The wrapper rejects a row that carries another arm's label, or none.

The Cypher arm is a seam only and raises `RetrievalUnavailableError`. Keep
`CYPHER_ARM_ENABLED` off and do not route to it, as your v0.4 setup already
does.

**Citations.** The citations `/query` returns carry `section_id`, `fact_id`,
`chunk_id` and `accession_number`, but not `claim_id` or `retrieval_method`.
Add both, so a citation still says which arm and which row it came from. PR #11
(citation schema) is still open; carry the same fields there.

**Decision: you key on these three strings. Tell us if you key on anything else.**


## 8 · Four failures, one of which you should never retry

| What happened | Example | What we raise |
|---|---|---|
| The call was wrong | `filters={"nonsense": 1}` | `ValueError` |
| The arm cannot serve that filter | `is_table` on the graph arm | `FilterNotSupportedError` |
| The model wrote a query that will not run | bad syntax, unknown label | `QueryGenerationError` |
| The store is unreachable | Aura down or cold | `RetrievalUnavailableError` |

**Done 22 Nov:** `describe_retrieval_error` now sorts failures into these
types, and `run_tool_selection` stops on `invalid_request` and
`unsupported_filter`.

Still open:

- On `RetrievalUnavailableError` you move to the alternate arm. Retry the
  **same** arm once first: Aura's first call after idle always fails this way
  (§11), and the alternate arm is usually the wrong arm for the question.
- Treat `ValueError` and `FilterNotSupportedError` the same way for filters.
  Today the two arms can answer the same bad filter with different errors.
- The fallback-mode `graphrag_node` still catches bare `Exception`. If you keep
  only auto mode (§4), this goes away.

**Decision: retry only `RetrievalUnavailableError`, once, on the same arm.**


## 9 · What "no evidence" looks like, and who says "I don't know"

Call `graph_search` / `structured_search` and you get a list. Empty means
nothing matched. Call `search(query, tool=...)` and you get `request_id`,
`trace_id`, `status`, `reason`, `results`. An arm that finds nothing returns
`status="abstain"`, `reason="missing_fact"`.

Statuses are `answer`, `clarify`, `abstain`, `block`. Reasons are
`out_of_scope`, `false_premise`, `fabricated_relationship`, `missing_fact`,
`ambiguous`. All describe the question, not whether an arm is running. An arm
that is off raises `RetrievalUnavailableError` instead.

Our arms can say a fact is missing; they cannot say a question is unanswerable.
Your v0.4 now abstains with a reason for comparisons, rankings and relationship
questions (`retrieval_limitation`). Use the same field for the plain
`NO_EVIDENCE` case, so the user always sees why.

**Decision: the final "I don't know" is composed on your side.**


## 10 · You cannot traverse the graph through anything we have given you

Layer 2 of `schema-ids-operations.md` lists eleven operations. Built:
`lookup_fact`, roughly, as `structured_search`; and RAG's `search_text`. Not
built: everything else, including all four graph operations — `neighbours`,
`find_path`, `events_between`, `community_summary`. Your v0.4 already abstains
on traversal questions, which is correct for now.

Candidates, in our order:

- **`neighbours(entity, relation_types, depth)`.** One parameterised Cypher
  behind the envelope you already consume. Walks `OWNS`, `MENTIONS` and
  `HAS_SEGMENT`. No model.
- **`trend` and `segment_breakdown`.**
- **`max_hops`** is validated and never applied; do not rely on it.

Company lookup ("Apple" → `AAPL`) is not exported by us. Keep resolving
companies with your own `COMPANY_TICKERS`.

**Decision: tell us which of the eleven you would actually call, in your order.**


## 11 · Two things that look like bugs and are not

**Dimensions are a list on the real arm and a dict on the mock.** Both shapes
validate, so a key built on one silently never matches the other.

```python
# mock
{"StatementBusinessSegmentsAxis": "Intelligent Cloud"}
# real
[{"axis": "us-gaap:StatementBusinessSegmentsAxis",
  "member": "msft:IntelligentCloudMember", "type": "explicit"}]
```

**Aura cold-starts.** The first call after idle raises
`RetrievalUnavailableError`. Retry once (§8), and pre-warm before any demo.


# The bigger picture: what the Agent layer still has to deliver

The path a question needs, and where each step stands.

| Step | Needed for | State 23 Nov |
|---|---|---|
| Understand the question | Company, period, metric, question type | **Done:** ten tickers; optional LLM query understanding (off by default) |
| Resolve to our vocabulary | "Apple" → `AAPL`; "Q3 FY2026" → a filing window | Company: your ticker map. Period: our arms. Quarter-only questions still fail (§2) |
| Choose a tool | Relational and numeric questions to the arm that can answer them | **Done** in auto mode; subsidiary route missing (§1) |
| Decompose | Comparisons, multi-company, multi-period | Abstains. RAG `retrieve_multi` still on a branch; our arms take lists (§2) |
| Retrieve with retries | Outage vs bad call vs bad filter | Failures classified; same-arm retry missing (§8) |
| Judge evidence | Enough to answer, or abstain | Narrative: provenance + filter match. Financial: regex shapes, rejects our rows (§1) |
| Compute | Growth, ratios, differences, with both inputs cited | Abstains; no owner (§5) |
| Compose the answer | The answer the user reads | Answer node built, **never run with a live model** (§3) |
| Cite | Every number and claim traceable to a filing | SEC links validated; `claim_id` / `retrieval_method` missing (§7); PR #11 open |
| Abstain honestly | "I don't know" with a reason | Reason for calculations and relationships; none for plain `NO_EVIDENCE` (§9) |
| Trace | Eval scoring, debugging | **Done** |
| Measure | INT-3: does routing help? | Ten-question local traces only. Gold set not run through the agent (§3) |