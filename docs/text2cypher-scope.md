# Text2Cypher — scope

19 November 2026. Owner: Adeline. Shadow: Alison. Reviewer: Kelly.

**Revised. This replaces the 15 November version of this document.** The decision
it asks for is to build `neighbours()` first and move Text2Cypher behind it. The
15 November scope is kept below the line, because the reasoning in it is still
good and only one of its premises turned out to be false.

## What changed

Three things, all measured since the scope was written.

**The graph holds subsidiaries.** The old scope's boundary — *"Not subsidiaries.
No `Subsidiary` node exists in the live graph, so those questions have no path"* —
is false. Counted on the shared instance, 19 November:

| Node | Count |
| --- | --- |
| `Claim` | 10,774 |
| `Subsidiary` | **752** |
| `Section` | 48 |
| `Filing` | 20 |
| `Company` | 10 |
| `RiskFactor` | 10 |

| Relationship | Count |
| --- | --- |
| `SUPPORTS` | 10,774 |
| `CONCERNS_RISK` | **6,198** |
| `OWNS` | **752** |
| `CONTAINS` | 48 |
| `FILES` | 20 |

Exhibit 21 loaded while nobody was looking. `OWNS` and `CONCERNS_RISK` together
are 6,950 edges that no retrieval path in this repo touches.

**Nothing traverses.** Layer 2 of `schema-ids-operations.md` is an operation
catalogue agreed with the Agent team in W4–W6. Eleven operations; two are built,
and **all four graph operations are not**. `graph_search` reaches a claim through
the full-text index and then walks three hops *upward* to fill the envelope. It
never follows `OWNS` or `CONCERNS_RISK`. So there is no traversal available to
anyone through our contract, and there has not been since W4.

**Somebody wrote one anyway.** #86 adds an ownership guard that walks `OWNS` to
answer *does X own Y?* — the first traversal in the arm. It is a regex fitted to
one sentence, and the valuable thing underneath it is the Cypher, not the regex.

## Why this is a rescope and not a delay

Text2Cypher's claim is that **a generated traversal answers questions the fixed
query cannot, often enough to justify the cost of generating it.** That claim is
still worth testing. It is being tested in the wrong order.

A generated traversal is being proposed before a written one exists. If
`neighbours()` lands and answers the questions we can already name, Text2Cypher's
claim narrows to the questions we *cannot* name — which is the version of the
claim actually worth two weeks. If `neighbours()` lands and the questions still
miss, that is a far cheaper way to learn it.

The evidence pointing at Text2Cypher is also thinner than the old scope assumed,
and two of its three supports have gone:

- **M18 is weaker than it reads.** The fixed query cannot express it, which is
  true. But the ten-theme risk taxonomy is coarse enough that the intersection of
  Microsoft and Apple is three generic buckets, and the Agent can already reach it
  with two filtered calls. It is a demo question, not a measurement.
- **There is no measurement of traversal failure.** The claim that none of Eval's
  25 number misses was a traversal failure does not exist — `side_by_side.json`
  records `not_run` for the graph arm in that comparison. Eval's real categories
  are `in_top50_ranked_below_8` (13), `multi_input_not_all_in_top50` (10) and
  `single_input_not_in_top50` (2). **None of them is a traversal category**, so
  nothing in round 1 says Text2Cypher would have helped — and nothing says it
  would not.
- **The filter path is broken underneath both.** Issue #94: the graph arm asks
  the full-text index for `top_k * 10` claims and applies the filter afterwards.
  A one-company, one-quarter filter returns **0 rows at `top_k=8`**, which is what
  Eval runs. Whatever we build on top of `graph_search` inherits that until it is
  fixed.

**The honest version of the position:** step 1 of the adapter is not started and
it is a W9 deliverable. Rescoping is partly a response to that. It is still the
right call, and it is cheaper to say both things than to let the second one be
discovered.

## What is proposed instead

**W9 — `neighbours(entity, relation_types, depth)`, from the Layer 2 catalogue.**

One parameterised Cypher. No model, no prompt, no cache, no guard against
generated text. It returns a subgraph with provenance, in the same envelope every
other arm returns, and it closes the first of the four unbuilt graph operations.

What it reaches on day one, from the counts above:

- the nine Exhibit 21 ownership questions, through `OWNS`
- *is Y a subsidiary of X*, *which subsidiaries does X own*, *does X own anything
  in Ireland* — the phrasings #86's regex misses
- M14, with #86's guard as one caller rather than its own implementation
- risk traversal through `CONCERNS_RISK`, which is 6,198 edges nothing reads

**W10 — Text2Cypher step 1, if the week has room.** The seam is already built
behind #67 and is inert until something registers a backend, so keeping it costs
nothing and keeps `cypher_search` a separate tool from `graph_search`. Step 1 is
unchanged in shape: the projection and hydrator proving the envelope end to end
against a stubbed model, no LLM call.

**At the freeze — the claim is settled on numbers, or the arm is cut.** Unchanged
from the old scope, and more likely to be reached now that it is not first in the
queue.

## The dependency on #86, stated plainly

**If Alison generalises the guard into `neighbours()`**, it is hers, the W9 item
is review rather than build, and this document's W9 line is already most of the
way done.

**If she keeps the narrow version**, `neighbours()` is mine, her regex becomes one
caller of it, and the docstring names it a stopgap so nobody finds it in a month
and takes it for the design.

Either is fine. Both need the answer before the W9 item can be assigned, and it is
the only thing in here that is blocking.

## Two interface questions — still open, still Alison's

Both move from Text2Cypher to `neighbours()` unchanged, because a written
traversal has the same two problems a generated one does:

1. **Does a refusal raise, or return a reason the adapter turns into `[]`?** The
   adapter plan assumes the second, because a refusal is an answer and not an
   outage. #86 answers this for abstention and the two should agree.
2. **What one line does the log write per call?** The question, what was walked,
   row count, latency. For Text2Cypher it also carries the generated Cypher,
   whether it parsed and whether the guard passed. Built from the first call
   rather than added once the shape is already wrong.

## Boundaries that are not negotiable

Unchanged, and the first three now apply to `neighbours()` as well.

- **The model never writes the `RETURN`.** It produces a fragment binding `c` to a
  `Claim`; the adapter appends the projection the graph arm already uses. Envelope
  conformance and the one-provenance-key rule then hold by construction rather
  than by inspection. `neighbours()` uses the same projection, which is most of
  why doing it first is cheap.
- **Every generated query runs in a read transaction.** The Aura credential can
  write and we cannot mint a read-only user on this tier, so the server does the
  refusing and the guard explains it. Two layers, because a parser we wrote is not
  a thing to stake the database on.
- **Retrieval returns evidence, not numbers.** Aggregation and counting stay out.
  An arm returning a figure instead of evidence is outside the contract.
- **A parse failure is counted, never swallowed.** Valid-Cypher rate is E2's
  headline measure and it cannot be recovered after the fact.
- **No automatic retry on a charged request.** One follow-up carrying the error,
  then stop. Eval re-runs 155 questions and sweeps more than once; the extraction
  pipeline already learned this the expensive way.

## Open, carried from the plan

Unchanged, and all three are now W10 questions rather than this week's.

- **3a.** Hand-written schema block in the prompt, or generated from the database
  at startup? Lean: hand-written and version-stamped. Generated cannot drift, but
  it would describe `Subsidiary` the day Exhibit 21 lands and change the prompt
  with nobody reviewing it. ⚠️ **Exhibit 21 has now landed**, which is the first
  live instance of exactly that.
- **5a.** Which model? Matching the extraction pipeline's OpenAI key is the cheap
  path, and it is the only key on this machine. Worth a moment because E7 is
  partly a model comparison and the cache is keyed on the choice.
- **6a.** Few-shot examples as literal Cypher, as question-to-path descriptions,
  or both with E3b deciding?

---

## The 15 November scope, kept for the record

Owner: Adeline. Shadow: Alison. Reviewer: Kelly.

### Why this exists at all

The graph arm runs one fixed query. It is a good query, and it is the only one it
has. A question whose shape the query does not anticipate gets the full-text index
and nothing else, which means the graph is doing the work of a keyword search on
most of the gold set.

Text2Cypher writes a query per question. The claim being tested is narrow and worth
stating as a claim rather than an assumption: **a generated traversal answers
questions the fixed query cannot, often enough to justify the cost of generating
it.** M18 — which risk factor do Microsoft and Apple both disclose — is the question
that made this concrete, because the fixed query cannot express it and the graph
plainly contains the answer.

If the measurement says otherwise, this arm is a cut candidate at the freeze and we
will have learned that for the price of two weeks. That is an acceptable outcome and
it should be said out loud now, not discovered in December.

### What it is not

- **Not a replacement for the graph arm.** It lands as a third tool, `cypher_search`,
  behind PR #67. Two names, because Eval is scoring the graph arm now and a flag that
  changed that arm's behaviour mid-round would give them one label covering two
  systems.
- **Not a Milestone A step and not on the round 1 path.** Nothing Eval is scoring
  this round depends on it.
- **Not numbers.** Aggregation and counting stay out of v1. Retrieval returns
  evidence and the Agent composes; an arm returning a figure instead of evidence is
  outside the contract.
- ~~**Not subsidiaries.** No `Subsidiary` node exists in the live graph, so those
  questions have no path and must return nothing rather than something plausible.~~
  **False as of 18 November — 752 `Subsidiary` nodes and 752 `OWNS` edges are
  loaded. This is the premise that collapsed and took the ordering with it.**

### The split

The shape of the split follows what each part can be tested against. Alison's two
pieces both need the adapter's interfaces to exist first, so she is reading and
deciding this week, not building.

| Part | Owner | Depends on |
| --- | --- | --- |
| Projection, hydrator, the stubbed-model path | Adeline | nothing — starts now |
| Read transaction and the per-call log record | Adeline | the projection |
| Prompt assembly, the real model call, the cache | Adeline | the log, so cost is visible from the first call |
| The guard — what a generated query is allowed to touch | Alison | the log record's shape |
| The query log as Eval consumes it | Alison | the same |
| The whole arm, after it runs | Alison | handover |

**Shadowing means reviewing every PR in the sequence**, not watching. The handover at
the end is a formality if Alison has read each step and a re-derivation if she has
not. She has three other W9 items, so the reviewing is the commitment being made
here — the building is not.

### Done, at three levels

- **This week.** Step 1 merges on its own: the projection and hydrator proving the
  envelope end to end against a stubbed model, with the failure table tested offline.
  No LLM call. Reviewable by someone who does not want to think about prompts.
- **The arm works.** `cypher_search` answers M18 with real evidence, refuses a write,
  falls back rather than crashing on a broken generation, and writes one log row per
  question.
- **The claim is settled.** E7 and E2 have numbers, and the freeze decision on
  whether this arm ships is made on them rather than on how interesting it was to
  build.
