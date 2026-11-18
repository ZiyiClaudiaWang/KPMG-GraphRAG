# Agent team call — 18 November 2026

Eight things to agree. Each ends in a decision.

**Start at section 1.** Your GraphRAG adapter is written but imports the wrong
module and is not in the workflow path, so you have one retriever and nothing
to route between. One line and a file move. Most of this page depends on it.

---

## 1 · Your adapter imports the wrong module, and nothing calls it

You already wrote the adapter. `demo/03_GraphRAG_graphrag_adapter.py` calls
both our arms, passes the arguments correctly, and keeps our provenance when
it normalises. Two things stop it working, and one of them is one line.

**It imports the wrong module.**

```python
# demo/03_GraphRAG_graphrag_adapter.py, in real mode
importlib.import_module("graphrag.retrieve")   # raises on every call
importlib.import_module("graphrag.backends")   # this one
```

`retrieve` holds the contract and waits for something to register a backend.
`backends` registers both arms from the environment as it loads. Import the
first and you get a working function that raises every time, which is what
real mode has been doing.

**Nothing calls it.** It lives in `demo/` and `integration.py` wires
`retriever=retrieve_evidence` from `rag_tool.py`. Moving it into
`agent/tools/` and passing it as the retriever is the rest of the work.

You do not need to translate anything else for us. Your `retrieval_node`
already calls `retriever(query=…, filters=…, top_k=…)`, which is our signature
with `trace_id` left at its default, and our arms already return the agreed
envelope. Keep `_to_evidence` only while RAG's shape differs from it — that is
a RAG question, not ours.

Two more things to know:

- We have no `pyproject.toml`, so you cannot pip install us. Put the checkout
  on `PYTHONPATH` or add a submodule. Ours to fix, this week.
- Graph needs `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`,
  `NEO4J_DATABASE`. Structured needs `MSFT_FACT_DB_PATH`. Call
  `backends.status` at startup and log it — it names every arm and why any one
  did not start.

**This gap is ours, not yours.** Both arms only started working on 10
November, the day of your last commit, and until today you had no credentials
and no import guidance. You wrote an adapter against a contract you could not
run.

**Decision: change the import, move the file, wire it in. Tell us if anything
in the envelope does not fit once it runs.**

---

## 2 · Every row says which arm produced it

| Arm | `metadata.retrieval_method` |
|---|---|
| `graph_search` | `neo4j_fulltext_claim` |
| `structured_search` | `duckdb_concept_match` |
| `cypher_search` | `neo4j_text2cypher` |

Until this week nothing checked this. A row could claim another arm's label
and pass every validator. Eval groups by this key and you will route on it, so
a wrong label makes both measure the wrong system with nothing raising. The
wrapper now rejects a row carrying another arm's label, or no label. PR #67,
green, not merged.

**Decision: you key on these three strings. Tell us if you key on anything
else.**

---

## 3 · Four failures, one of which you should never retry

| What happened | Example | What we raise |
|---|---|---|
| The call was wrong | `filters={"nonsense": 1}` | `ValueError` |
| The arm cannot serve that filter | `fiscal_year` on the graph arm | `FilterNotSupportedError` |
| The model wrote a query that will not run | bad syntax, unknown label | `QueryGenerationError` — new |
| The store is unreachable | Aura down or cold | `RetrievalUnavailableError` |

Only the last is worth retrying. The third does not exist on `main` yet — a
bad generated query comes back today as `RetrievalUnavailableError`, so you
would retry something that can never succeed.

Your side: `retrieval_node` catches bare `Exception` and `determine_status`
returns `SUCCESS`, `NO_EVIDENCE` or `ERROR`. All four arrive as `ERROR`.
Splitting out the retry case is a change to one function.

**Decision: retry only `RetrievalUnavailableError`.**

---

## 4 · What "no evidence" looks like, and who says "I don't know"

Call `graph_search` / `structured_search` / `cypher_search` and you get a list.
Empty means nothing matched. Call `search(query, tool=...)` and you get
`request_id`, `trace_id`, `status`, `reason`, `results`. An arm that finds
nothing returns `status="abstain"`, `reason="missing_fact"`.

Statuses are `answer`, `clarify`, `abstain`, `block`. Reasons are
`out_of_scope`, `false_premise`, `fabricated_relationship`, `missing_fact`,
`ambiguous`. **All describe the question, not whether an arm is running.** An
arm that is switched off raises `RetrievalUnavailableError` instead — saying a
question was out of scope when an arm is simply off is a lie you would act on.

We can tell you a fact is missing. We cannot tell you a question is
unanswerable. Eval has said the same to us.

**Decision: the final "I don't know" is composed on your side.**

---

## 5 · Your parser and our corpus know different companies

From `agent/query_parser.py`. Parsing is three regexes, no model, returning
`ticker`, `fiscal_year`, `fiscal_period`.

**The ticker lists barely overlap.** Yours holds microsoft, apple, google,
alphabet, amazon, meta, nvidia, tesla. Ours is these ten and only these ten:

```python
COMPANY_TICKERS = {
    "apple": "AAPL",            "casey's": "CASY",
    "mckesson": "MCK",          "medtronic": "MDT",
    "microsoft": "MSFT",        "micron": "MU",
    "nike": "NKE",              "netapp": "NTAP",
    "parker hannifin": "PH",    "take-two": "TTWO",
}
```

Overlap today is MSFT and AAPL. Five of yours have no filings here. Eight of
ours return `None` from `_extract_ticker`, so no filter is set and the search
runs across every company — ask about Medtronic, get Microsoft.

Your comment says to keep the list in sync with what RAG retrieves. It is in
sync with them and not with us, and nobody wrote our list down anywhere.

**Both period filters you emit are ignored, and that is ours.** `fiscal_year`
and `fiscal_period` are accepted by the contract and applied by neither arm.
The structured arm also ignores `section` and `is_table`. They pass
validation, never reach the query, and nothing raises — so a question about
FY2023 gets claims from any year that look filtered and are not. Our own code
comment warns about this three lines above the set that does it. We will apply
them or refuse them by name. Until then send `reporting_period_end` or `form`.

Smaller: `query_parser.py` special-cases `MSFT + "March 31, 2026" → Q3
FY2026`. That is fitted to the mock's single filing.

**Decision: the ticker list is sent. Run `python3 -m graphrag.vocabulary` for
the filter list. You tell us whether the period keys should be applied or
refused.**

---

## 6 · You cannot traverse the graph through anything we have given you

Layer 2 of `schema-ids-operations.md` is an operation catalogue we agreed with
you in W4–W6. Eleven operations. Built: `lookup_fact`, roughly, as
`structured_search`; and RAG's `search_text`. **Not built: everything else**,
including all four graph operations — `neighbours`, `find_path`,
`events_between`, `community_summary`.

So the graph arm exposes one operation out of four, and it is lexical search.
The 752 `Subsidiary` nodes and 752 `OWNS` edges are loaded on both instances
and unreachable through any contract we have given you.

What works today is two filtered calls and an intersection on your side. That
is how "which risk factor do Microsoft and Apple both disclose" is answerable.
It puts graph logic in your code rather than ours.

What we think to add, in order:

1. **`neighbours(entity, relation_types, depth)`** — one parameterised Cypher
   behind the envelope you already consume. No model, no prompt, no token
   cost. Walks `OWNS`, which answers the Exhibit 21 questions.
2. **A company lookup.** You have "Apple" and every filter wants a ticker. The
   matching logic sits in `graphrag/entity_resolution.py` and is not exported,
   so you had to write regexes. Ours to fix.
3. **A list of valid filter values** — tickers, forms, sections, risk themes.
   A wrong value returns empty, which you cannot tell from a missing fact.
4. **`trend` and `segment_breakdown`.** `trend` is most of PR #74
   (`fact_series.py`), already open. `segment_breakdown` is on Ashley's card.
5. **`max_hops`** is validated and never applied, because there is no
   traversal to bound. Refuse it until `neighbours` exists.

Text2Cypher writes a query per question and covers shapes nobody enumerated.
`neighbours` covers the ones we can name, for far less. Our read: 1 and 2
unblock you this week; the generated arm is a W10 bet.

**Decision: tell us which of the eleven you would actually call, in your
order.**

---

## 7 · Who owns the arithmetic

Eval compared RAG and our structured arm on **one Microsoft 10-Q**
(`0001193125-26-191507`), `top_k=8`. The graph arm was not run — their
`not_run` note says so — so nothing here says anything about traversal either
way. Of 25 number misses:

| Eval's category | Count |
|---|---|
| `in_top50_ranked_below_8` | 13 (6 of them the period-word bug) |
| `multi_input_not_all_in_top50` | 10 |
| `single_input_not_in_top50` | 2 |

All 25 are ranking or composition failures in the structured arm. None would
be fixed by generating Cypher — worth saying because routing numeric misses to
the generated arm is a tempting wrong move.

**The ten multi-input ones have no owner.** Each needs two retrievals and an
arithmetic step, and our two documents disagree about whose step it is. Layer
2 lists `compare_periods`, `ratio` and `trend` as Structured operations —
computed at query time, returning the derived number with both inputs cited,
never stored, because a stored growth rate has no XBRL provenance and goes
stale when a 10-K/A restates an input. The arm as built returns facts only, so
today you compose, by default rather than by decision.

**Decision: pick one. If ours, we build the three operations. If yours, we
delete those rows from Layer 2 so it stops promising what nobody is building.**

---

## 8 · The routing table needs revising

Layer 3 of `schema-ids-operations.md`, written in W6 and untouched since.

| Type | Route today |
|---|---|
| Local lookup | Vector |
| Numeric lookup | Structured |
| Numeric comparison | Structured |
| Relational / multi-hop | Graph |
| Hybrid | Graph resolves the event, structured supplies numbers, vector supplies prose |
| Global synthesis | Community summaries |

Three rows do not survive:

- **Relational / multi-hop → Graph.** The arm does no traversal. See 6.
- **Its subsidiary example.** The data is there — both instances hold 752
  `Subsidiary` nodes, measured this morning — but the arm matches claim text
  and never walks `OWNS`, so you get a sentence mentioning a subsidiary, not
  the relationship.
- **Global synthesis → Community summaries.** We never built them.

A tool-choosing node belongs before retrieval, not after. Rules are enough to
start:

```python
def choose_tool(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ("subsidiary", "subsidiaries", "owns", "acquired")):
        return "graph"
    if any(w in q for w in ("revenue", "income", "assets", "margin", "eps",
                            "cash flow", "how much")):
        return "structured"
    return "vector"
```

Measure it before trusting it. The share of questions reaching the right arm,
before and after, is what makes INT-3 real.

One more, worth deciding early because it changes state rather than a node:
your graph is linear and `AgentGraphState` holds a single `evidence` field, so
a second retrieval overwrites the first. The ten multi-input questions need an
accumulator and an edge back to retrieval.

**Decision: revise these rows now, against what the arms actually do.**

---

## Three things that look like bugs and are not

**Dimensions are a list on the real arm and a dict on the mock.** Both shapes
validate, so a key built on one silently never matches the other.

```python
# mock
{"StatementBusinessSegmentsAxis": "Intelligent Cloud"}
# real
[{"axis": "us-gaap:StatementBusinessSegmentsAxis",
  "member": "msft:IntelligentCloudMember", "type": "explicit"}]
```

The real one carries namespaces and tag names. Matching on `"Intelligent
Cloud"` finds nothing.

**`graphrag.backends`, not `graphrag.retrieve`.** Covered in section 1 and
worth repeating, because the failure is a function that raises on every call.

**Aura cold-starts.** The first call after idle raises
`RetrievalUnavailableError`. That is wake-up latency. Pre-warm before any demo
or you will report an outage that is not one.

---

## Two corrections to `docs/agent-notes.md`

Dated 13 October. Two things in it are now wrong, and we will fix both.

- "An exception means retrieval is broken." Only one of the four in section 3
  means that.
- "Connecting to Neo4j is a card this week." Done on 10 November.

---

## What we owe you, and what we need back

| | What | When |
|---|---|---|
| Ours | ~~Neo4j credentials, out of band~~ | **sent** |
| Ours | ~~The ten tickers~~ | **sent** |
| Ours | Which filters we apply, ignore and refuse — `python3 -m graphrag.vocabulary` | today |
| Ours | PR #32's import guidance, pointing at `backends` not `retrieve` | today |
| Ours | `fiscal_year` and `fiscal_period` applied or refused, never ignored | this week |
| Ours | PR #67 merged, so the error taxonomy is real | this week |
| Ours | A `pyproject.toml`, so we can be installed rather than path-hacked | this week |
| Ours | Layer 3 revised, `agent-notes.md` corrected | this week |
| Ours | `neighbours`, if you say it is the one you need | next week |
| Yours | Change the import, move the adapter into `agent/tools/`, wire it in | this week |
| Yours | Widen the ticker map, swap the period keys | this week |
| Yours | Split the retry case out of `ERROR` | this week |
| Yours | Which Layer 2 operations you would actually call, in your order | today |
| Yours | Whether `AgentGraphState` accumulates evidence across calls | this week |
| Both | Who owns the arithmetic on multi-input questions | today |
