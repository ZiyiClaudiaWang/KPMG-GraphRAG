# Agent team call — 18 November 2026

What we want to leave this call with: five things agreed, not five things
discussed. Each one below has a decision line in bold. Everything else on the
page is the evidence behind it.

Three of the five are changes to how our tools fail. They matter now because
you are about to write the code that reacts to those failures, and two of the
four cases are indistinguishable today.

---

## 1 · Every row says which arm produced it

Our evidence rows carry `metadata.retrieval_method`. There are three values and
no others:

| Arm | Value |
|---|---|
| `graph_search` | `neo4j_fulltext_claim` |
| `structured_search` | `duckdb_concept_match` |
| `cypher_search` | `neo4j_text2cypher` |

Until this week nothing checked it. A row could claim to come from an arm that
did not produce it and every validator passed. Eval groups by this key and you
will route on it, so a mislabelled row makes both routing accuracy and scoring
measure the wrong system, with nothing raising.

The wrapper now rejects a row whose label belongs to another arm, and a row
with no label at all. That is on PR #67, green, not yet merged.

**Decision: you key on these three strings, and tell us if you are keying on
anything else.**

---

## 2 · Four failures, four different moves

Right now two of these are the same exception, so you cannot tell them apart.

| What happened | Example | What we raise | Your move |
|---|---|---|---|
| The call was wrong | `filters={"nonsense": 1}` | `ValueError` | Fix the call. Never retry. |
| The arm cannot serve that filter | `fiscal_year` on the graph arm | `FilterNotSupportedError` | Try the other arm. |
| The model wrote a query that will not run | bad syntax, unknown label | `QueryGenerationError` — **new** | Do not retry. Count it. |
| The store is unreachable | Aura is down or cold | `RetrievalUnavailableError` | Retry. |

The third case does not exist on `main` today — a bad generated query comes back
as `RetrievalUnavailableError`, which the contract defines as an outage worth
retrying. You would retry something that can never succeed, and the count of
those failures is the measure of whether the Text2Cypher arm works at all.

`QueryGenerationError` is on PR #67 with the rest.

**Decision: agree the fourth row is separate before you write retry logic, so
the retry rule is "retry only `RetrievalUnavailableError`".**

---

## 3 · What "no evidence" looks like, and who says "I don't know"

This changed last week and it is the one most likely to surprise you.

- Call `graph_search` / `structured_search` / `cypher_search` and you get a
  plain list, exactly as before. Empty means nothing matched.
- Call `search(query, tool=...)` and you get an envelope:
  `request_id`, `trace_id`, `status`, `reason`, `results`. An arm that finds
  nothing comes back as `status="abstain"`, `reason="missing_fact"`.

The statuses are `answer`, `clarify`, `abstain`, `block`. The reasons are
`out_of_scope`, `false_premise`, `fabricated_relationship`, `missing_fact`,
`ambiguous`.

**All of them describe the question, not whether an arm is running.** An arm
that is switched off does not come back as `abstain` — it raises
`RetrievalUnavailableError`, because telling you a question was out of scope
when the real answer is "that arm is off" is a lie you would act on.

Eval found that neither retriever returns empty on the seven unanswerable
questions in their set. We can tell you a fact is missing. We cannot tell you a
question is unanswerable.

**Decision: the final "I don't know" comes from your evidence check, not from
us. Say it out loud so nobody is waiting on us to build it.**

---

## 4 · Routing — what round 1 actually says

Eval diagnosed the 25 number questions our side missed
(`results/side_by_side.json`, `number_miss_diagnosis`):

| Cause | Count |
|---|---|
| Ranked below the top 8 | 13 |
| Needed more than one call to calculate | 10 |
| Missing concept alias | 2 |
| **Traversal failure** | **0** |

None of them was a traversal failure. **So Text2Cypher fixes none of the
structured arm's current misses.** The generated-Cypher arm is for questions
the one fixed graph query cannot express — "which risk factor do Microsoft and
Apple both disclose" — not a fallback for numbers that came back wrong. If your
routing plan sends numeric misses to it, that is the thing to catch today.

The ten multi-concept misses are worth a minute on their own: they are
questions needing two retrieval calls and an arithmetic step. That is an Agent
composition problem, not a retrieval one, and it is the largest single bucket
we are not addressing.

### The routing table, for revision on the call

This is Layer 3 of `schema-ids-operations.md` as it stands. It was written in
W6 and has not been touched since round 1.

| Type | Example | Route today |
|---|---|---|
| Local lookup | "What did Apple say about supply chain risk in 2023?" | Vector |
| Numeric lookup | "What was Apple's R&D spend in FY2023?" | Structured |
| Numeric comparison | "How did R&D spend change between FY2022 and FY2023?" | Structured |
| Relational / multi-hop | "Which subsidiaries did Apple acquire, and what did they report?" | Graph |
| Hybrid | "How did R&D spend change after the 2023 acquisition?" | Graph resolves the event, structured supplies numbers, vector supplies prose |
| Global synthesis | "What themes dominate risk disclosure across the corpus?" | Community summaries |

Three rows do not survive contact with what we built:

- **Relational / multi-hop → Graph.** `graph_search` does no traversal. It is
  one full-text match on Claims over a fixed three-hop path to fill the
  envelope. It cannot follow a relationship the question names.
- **The subsidiary example.** `Subsidiary` nodes exist on our internal
  instance only. The instance you and Eval are given has none, so that example
  returns nothing for you today.
- **Global synthesis → Community summaries.** We have not built community
  summaries. That row routes to something that does not exist.

**Decision: revise these rows now, on the call, against what the arms do.**

---

## 5 · The third tool is real and switched off

`cypher_search` will appear beside the other two in `backends.status()`. It is
**off unless `CYPHER_ARM_ENABLED=1`**, and reports itself as skipped with that
reason.

It is inert this week. What ships is the projection and the hydrator against a
stubbed model — no language model is called. Do not plan around it answering
anything before the next call.

**Decision: nothing to decide. Noted so it does not appear in your tool list
unannounced.**

---

## Three things that will read as a bug

Each of these is working as designed and looks broken the first time.

**Dimensions are a list on the real arm and a dict on the mock.** This is
silent — both shapes validate, and a key built on one will never match a key
built on the other.

```python
# mock/mock_retrieval_tools.py
{"StatementBusinessSegmentsAxis": "Intelligent Cloud"}

# the real structured arm, run today
[{"axis": "us-gaap:StatementBusinessSegmentsAxis",
  "member": "msft:IntelligentCloudMember",
  "type": "explicit"}]
```

Note the real one carries namespace prefixes and the tag name, not a display
name. Anything matching on `"Intelligent Cloud"` finds nothing.

**Import `graphrag.backends`, not `graphrag.retrieve`.** Both expose the same
function names. `backends` connects from the environment as it loads;
`retrieve` hands you a working function that raises on every call.

**Aura cold-starts.** The first call after the instance has been idle raises
`RetrievalUnavailableError`. It is wake-up latency, not an outage. Pre-warm
before any demo or you will report an outage that is not one.

---

## Two corrections to `docs/agent-notes.md`

That document is dated 13 October and two things in it are now wrong. We will
fix them; flagging so nobody builds on them this week.

- **"An exception means retrieval is broken."** True when it was written, not
  true now. Section 2 above has four exceptions and only one of them means
  broken.
- **"Connecting to Neo4j … is a card this week."** Done. Both arms connect from
  environment variables and have since 10 November.

---

## What we owe you, and what we need back

| | What | Who | When |
|---|---|---|---|
| Ours | PR #67 merged, so the error taxonomy is real and not a promise | Adeline, Kelly reviews | this week |
| Ours | Layer 3 revised with today's decisions | Adeline | this week |
| Ours | `agent-notes.md` corrected | Adeline | this week |
| Yours | Confirm you key on the three `retrieval_method` strings | | today |
| Yours | Confirm retry applies only to `RetrievalUnavailableError` | | today |
| Yours | Confirm abstention is composed on your side | | today |
