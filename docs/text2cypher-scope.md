# Text2Cypher — scope

15 November 2026. Owner: Adeline. Shadow: Alison. Reviewer: Kelly.

What this arm is for, who builds which part, and where it stops. The engineering
decisions behind it are in the adapter plan of 11 Nov; this document is the scope
wrapped around that plan, and it is the thing to disagree with before work starts.

## Why this exists at all

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

## What it is not

- **Not a replacement for the graph arm.** It lands as a third tool, `cypher_search`,
  behind PR #67. Two names, because Eval is scoring the graph arm now and a flag that
  changed that arm's behaviour mid-round would give them one label covering two
  systems.
- **Not a Milestone A step and not on the round 1 path.** Nothing Eval is scoring
  this round depends on it.
- **Not numbers.** Aggregation and counting stay out of v1. Retrieval returns
  evidence and the Agent composes; an arm returning a figure instead of evidence is
  outside the contract.
- **Not subsidiaries.** No `Subsidiary` node exists in the live graph, so those
  questions have no path and must return nothing rather than something plausible.

## The split

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

## Two interface questions, this week

Neither needs code, both block Alison's parts, and they are hers to settle:

1. **Does the guard refuse by raising, or by returning a reason the adapter turns
   into `[]`?** The adapter plan assumes the second, because a refusal is an answer
   and not an outage. If it raises, the failure table changes and so does what Eval
   sees when a question is refused.
2. **What one line does the log write per call?** The question, the generated Cypher,
   whether it parsed, whether the guard passed, row count, latency, tokens. That
   record is both her log and E2's measurement, so it is built from the first call
   rather than added once the shape is already wrong.

## Boundaries that are not negotiable

These are the ones where being wrong is expensive and quiet, so they are listed
separately from the plan's reasoning.

- **The model never writes the `RETURN`.** It produces a fragment binding `c` to a
  `Claim`; the adapter appends the projection the graph arm already uses. Envelope
  conformance and the one-provenance-key rule then hold by construction rather than
  by inspection.
- **Every generated query runs in a read transaction.** The Aura credential can
  write and we cannot mint a read-only user on this tier, so the server does the
  refusing and the guard explains it. Two layers, because a parser we wrote is not
  a thing to stake the database on.
- **A parse failure is counted, never swallowed.** Valid-Cypher rate is E2's headline
  measure and it cannot be recovered after the fact.
- **No automatic retry on a charged request.** One follow-up carrying the error, then
  stop. Eval re-runs 155 questions and sweeps more than once; the extraction pipeline
  already learned this the expensive way.

## Done, at three levels

- **This week.** Step 1 merges on its own: the projection and hydrator proving the
  envelope end to end against a stubbed model, with the failure table tested offline.
  No LLM call. Reviewable by someone who does not want to think about prompts.
- **The arm works.** `cypher_search` answers M18 with real evidence, refuses a write,
  falls back rather than crashing on a broken generation, and writes one log row per
  question.
- **The claim is settled.** E7 and E2 have numbers, and the freeze decision on
  whether this arm ships is made on them rather than on how interesting it was to
  build.

## Open, carried from the plan

- **3a.** Hand-written schema block in the prompt, or generated from the database at
  startup? Lean: hand-written and version-stamped. Generated cannot drift, but it
  would describe `Subsidiary` the day Exhibit 21 lands and change the prompt with
  nobody reviewing it.
- **5a.** Which model? Matching the extraction pipeline's OpenAI key is the cheap
  path, and it is the only key on this machine. Worth a moment because E7 is partly a
  model comparison and the cache is keyed on the choice.
- **6a.** Few-shot examples as literal Cypher, as question-to-path descriptions, or
  both with E3b deciding?
