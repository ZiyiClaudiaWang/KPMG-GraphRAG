# Agent team call — 18 November 2026

7 things we want to align and free on:

Three of the seven are changes to how our tools fail. They matter now because
you are about to write the code that reacts to those failures, and two of the
four cases are indistinguishable today.

Two are new since this page was first drafted. **Section 6**: there is no way
for you to traverse the graph through anything we have given you, and the
answer to that is an operation catalogue we already agreed, not the new arm.
**Section 7**: your parser and our corpus recognise two companies in common,
and the two period filters you send are silently dropped by both our arms. If
time runs short, start at 7 — it is the one that is costing you calls today.

---

## 1 · Every row says which arm produced it

Our evidence rows carry `metadata.retrieval_method`. There are three arms and three values:

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

**Decision: you key on these three strings. Let us know if you are keying on
anything else?**

---

## 2 · Four failures 

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
us. Confirming you are building it.**

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

### The ten multi-concept misses, and who owns them

This is the largest single bucket and nobody currently owns it. Each of these
questions needs two retrieval calls and an arithmetic step, and the two
documents we have disagree about whose step it is.

**Layer 2 says ours.** `compare_periods`, `ratio` and `trend` are listed as
Structured operations, under an explicit rule: *compute at query time, return
the derived number and both inputs, cite the inputs.* Never store a computed
value as a fact, because a stored growth rate has no XBRL provenance and goes
silently stale when a 10-K/A restates one of its inputs.

**The arm as built says yours.** It returns facts. Any question needing two of
them and a subtraction is composed on your side today, by default rather than
by decision.

Neither is obviously right. Ours keeps the arithmetic next to the provenance
and gives you one call instead of two. Yours keeps retrieval returning
evidence and nothing else, which is the rule the rest of the contract follows.

**Decision: pick one, today.** If it is ours, we build those three operations
and Layer 2 stands. If it is yours, we delete those rows from Layer 2 so the
catalogue stops promising something no one is building. What we should not do
is leave it as it is, where the document says one thing and the code does
another and ten questions fall through the gap.

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
- **The subsidiary example.** The data is there — measured this morning, both
  our Aura instances hold 752 `Subsidiary` nodes and 752 `OWNS` edges. What is
  missing is a way to reach them. The arm matches claim text and never walks
  an `OWNS` edge, so the question returns a sentence that mentions a
  subsidiary, which is not the relationship. See section 6.
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

## 6 · You cannot traverse the graph yet, and the fix is not the new arm

This is the longest item and the one worth the most time, because it is a
contract we already agreed and did not finish.

**Layer 2 of `schema-ids-operations.md` is an operation catalogue, agreed with
you in W4–W6.** Eleven operations. This is what is actually built:

| Operation | Arm | Built |
| --- | --- | --- |
| `lookup_fact(cik, concept, period, basis)` | Structured | roughly, as `structured_search` |
| `compare_periods`, `compare_entities`, `trend`, `ratio` | Structured | no |
| `segment_breakdown(cik, concept, period)` | Structured | no |
| `neighbours(entity, relation_types, depth)` | Graph | **no** |
| `find_path(a, b, max_hops)` | Graph | **no** |
| `events_between(cik, date_a, date_b)` | Graph | **no** |
| `community_summary(level, topic)` | Graph | no, and nothing to build it from |
| `search_text(query, filters)` | Vector | yes, RAG's |

So the graph arm exposes one operation out of four, and the one it exposes is
lexical search. **There is no traversal available to you through any contract
we have given you.** The 752 `Subsidiary` nodes and their `OWNS` edges are
loaded and unreachable.

What you can do today is two filtered calls and an intersection on your side —
that is how "which risk factor do Microsoft and Apple both disclose" is
answerable. It works, and it puts the graph logic in your code rather than
ours.

### What we think should be added, and in what order

1. **`neighbours(entity, relation_types, depth)`.** One parameterised Cypher
   behind the envelope you already consume. No model, no prompt, no guard, no
   token cost. It walks `OWNS`, which answers the Exhibit 21 questions
   directly, and it walks `CONCERNS_RISK`, which gets the shared-risk question
   down to one intersection. **This is the thing that makes "the Agent
   traverses the graph" true**, and it is days of work.
2. **A company lookup.** You have the string "Apple" and every filter wants a
   ticker or a cik. The matching logic exists in
   `graphrag/entity_resolution.py` and is not exported, so today you have to
   hardcode tickers. That is our gap, not yours.
3. **A way to list valid filter values** — which tickers, forms, sections and
   risk themes exist. A wrong filter value returns empty, which you cannot
   tell apart from a fact that is not there.
4. **`trend` and `segment_breakdown`.** These are the ten multi-concept misses
   from section 4. `trend` is most of PR #74, already open.
5. **`max_hops` is accepted and never applied.** Set it today and you get
   unbounded results and no error. Until `neighbours` exists to honour it, it
   should be refused rather than ignored.

### The part that changes our plan, not yours

Text2Cypher writes a query per question. `neighbours` and `find_path` cover
the traversals we can name in advance, at a fraction of the cost and with
nothing to guard. The generated arm is for the shapes nobody enumerated.

Read honestly: **1 and 2 unblock you this week; the generated arm is a W10
bet.** We would rather hear from you which of the eleven you would actually
call than build the general case first.

**Decision: tell us which operations you need, in your order, not ours. That
list is what we build next.**

---

## 7 · Your parser and our corpus do not know the same companies

Read from `agent/query_parser.py` on `main` this morning, so correct us if it
has moved. Three things, and the first costs you most of a routing test.

### The ticker lists barely overlap

`COMPANY_TICKERS` holds microsoft, apple, google, alphabet, amazon, meta,
nvidia, tesla. **Our corpus is these ten and only these ten:**

```python
COMPANY_TICKERS = {
    "apple": "AAPL",            "casey's": "CASY",
    "mckesson": "MCK",          "medtronic": "MDT",
    "microsoft": "MSFT",        "micron": "MU",
    "nike": "NKE",              "netapp": "NTAP",
    "parker hannifin": "PH",    "take-two": "TTWO",
}
```

The overlap today is **MSFT and AAPL**. Five of your seven have no filings in
our corpus at all, and eight of our ten are invisible to your parser:
`_extract_ticker` returns `None`, no filter is set, and the search runs across
every company. Ask about Medtronic and you can get Microsoft back.

Your comment says to keep the list in sync with what the RAG team retrieves.
It is in sync with them and not with us, which is nobody's mistake — it is
just not written down anywhere. We will publish the list; see section 6 item 3.

### Both period filters you emit are silently dropped

`understand_query` returns `ticker`, `fiscal_year`, `fiscal_period`. Our
contract accepts the last two and **neither arm applies them**. They are in
`_IGNORED_FILTERS`: they pass validation, never reach the query, and nothing
raises. A question about FY2023 gets claims from any year that look filtered
and are not.

**That one is ours, not yours.** We publish a filter we do not honour, which
is the exact hazard our own code comment warns about three lines above the set
that does it. We will either apply them or refuse them by name. Until then,
send `reporting_period_end` or `form` — those are applied.

### Your router has one error bucket

`determine_status` returns `SUCCESS`, `NO_EVIDENCE` or `ERROR`, so all four
failures in section 2 arrive as `ERROR`. Splitting the retry case from the
other three is a small change in that one function, and it is the whole of
what section 2 asks for.

Smaller, and only worth a sentence: `query_parser.py` special-cases
`MSFT + "March 31, 2026" → Q3 FY2026`. That is fitted to the mock's single
filing and will not survive the real corpus.

**Decision: we send you the ticker list and the applied-filter list today. You
tell us whether the period keys should be applied or refused.**

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
| Ours | `neighbours`, if you say it is the one you need | Adeline with Alison | next week |
| Ours | `max_hops` refused rather than silently ignored | Adeline | this week |
| Yours | Confirm you key on the three `retrieval_method` strings | | today |
| Yours | Confirm retry applies only to `RetrievalUnavailableError` | | today |
| Yours | Confirm abstention is composed on your side | | today |
| Yours | **Which Layer 2 operations you would actually call, in your order** | | today |
| Both | **Who owns the arithmetic on multi-concept questions** | | today |
| Ours | The ten tickers and the list of filters we actually apply | Adeline | today |
| Ours | `fiscal_year` and `fiscal_period` applied or refused, never ignored | Adeline | this week |
| Yours | Ticker map widened to the ten, period keys swapped | | this week |
| Yours | Split the retry case out of `ERROR` in `determine_status` | | this week |
