# What GraphRAG needs from Eval

Draft, 2026-10-13. Shortened 2026-10-22. For Yulei Ding and the Eval/RAG side.

> Read with [eval-ask-v2.md](./eval-ask-v2.md), which reviews Gold Set v1 against the
> nineteen questions below and names the filings each one can run against. Where the two
> disagree on the corpus, v2 is current.

## The ask

We are **not** writing a second gold set. A parallel benchmark in our own format would
split the harness, make scores incomparable, and leave us grading our own arm.

We want **15–20 questions added inside yours, in your format** — the shapes no existing
set reaches.

The gap is one shape: a question whose answer requires following a relationship to
something the question could not name. No "which subsidiaries", no "after the
acquisition", no entity that has to be resolved before it can be retrieved. Cross-company
questions in the current sets are fan-out, not traversal — independent lookups with a
ticker filter, compared afterwards. A tuned vector index does that well without a graph.

So if round 1 runs against the sets as they stand, the graph arm scores on work vector
already does, and we finish with no evidence the graph earned its place. That is a
coverage gap, not a criticism.

**We propose the shapes and the graph path each should exercise. Eval writes, validates
and owns the wording and the gold evidence.** We should not author the benchmark our own
arm is graded on — if the graph scores well on questions we wrote, nobody outside the team
will believe it.

## The nineteen candidates

Shapes and intent, not final wording. `X` and `Y` are two companies with overlapping
Exhibit 21 lists, `Z` is a segment, `S` is a subsidiary named in X's Exhibit 21.

| # | Shape | Question | Why a vector index cannot do it |
|---|---|---|---|
| M01 | Subsidiary | Which subsidiaries does X list in Exhibit 21, and how many are incorporated outside the United States? | Reading a list and aggregating by jurisdiction |
| M02 | Subsidiary | Do X and Y have subsidiaries incorporated in the same jurisdiction? | Two filings, two exhibits, one join |
| M03 | Subsidiary | Which of X's listed subsidiaries also file under their own CIK? | Resolving a name to a registrant in another filing |
| M04 | Across years | How did X's reportable segments change between FY2021 and FY2023? | Three filings aligned on a changing segment set |
| M05 | Across years | In which fiscal year did X first report segment Z? | Ordering filings, not retrieving one |
| M06 | Across years | Which risk factors appear in X's FY2023 Item 1A that were absent in FY2021? | Set difference across two Item 1As |
| M07 | Entity resolution | What was X called before FY2022, and what did it report under the earlier name? | One identity, two legal names |
| M08 | Entity resolution | After X acquired Y, in which filing did Y stop appearing as a separate registrant? | The acquisition event plus a filing-history join |
| M09 | Numeric + relational | How did X's R&D spend change in the two fiscal years after the 2023 acquisition? | Graph resolves the event date, structured supplies the values |
| M10 | Numeric + relational | Compare segment operating margin for X and Y in the same fiscal year. | Two companies, dimensional facts, a derived ratio |
| M11 | Numeric + relational | Which of X's segments contributed most to revenue growth between FY2022 and FY2023? | Dimensional facts plus arithmetic, total versus part |
| M12 | Amendment | What did X report as FY2023 revenue as filed, and what did the 10-K/A restate it to? | Both versions must survive ingestion |
| M13 | Amendment | Which figures changed between X's 10-K and its 10-K/A? | Needs `basis` and `amends_accession` |
| M17 | Deep traversal | Which segment absorbed S after X acquired it, and how did that segment's revenue move the next year? | Four hops, company to subsidiary to segment to fact. No chunk contains both ends — the link is in Exhibit 21 and the segment note, the number is in XBRL |
| M18 | Deep traversal | Which risk factor do X and Y both disclose, and what sentence does each use? | Five hops each side, meeting at a risk factor neither question names. Retrieved separately, two Item 1As return similar *passages*; the shared risk is what they have in common, not the wording |
| M19 | Deep traversal | Which of X's segments were affected by the figures its 10-K/A restated? | Three hops across the amendment. Segment attribution is a dimension on the fact, not a sentence in the text |

M17–M19 were added on 20 Oct, after the ontology merge settled two structures that nothing
above tested. They also carry the depth: most of M01–M16 resolves in one or two hops, so a
set without them cannot tell us whether expanding to three hops helps or just adds context
— and `max_hops` is a tool-contract parameter somebody has to pick a default for.

### Abstention controls — the ones that catch invention

**These matter more than the rest.** A graph that invents edges scores well on every
question above and fails only here.

| # | Question | Expected behaviour |
|---|---|---|
| M14 | Does X own Y? *(a pair with no ownership relationship)* | "No evidence", not a fabricated edge |
| M15 | Which subsidiaries did X list in a year whose 10-K is not in the corpus? | Abstain, state the gap |
| M16 | A question about a company absent from the corpus | `[]` and an abstention, not a plausible answer from a similar company |

Worth agreeing before round 1: a **provenance-correctness sample**. For N answers, does the
cited evidence actually support the claim? Provenance buys auditability, not accuracy — an
invented relationship still carries a valid key and a resolvable accession, so the citation
renders and the edge is wrong. Nothing in `evidence_recall` catches that.

## What the corpus has to contain

- **10-Ks, not only 10-Qs.** Exhibit 21 is filed with the 10-K. No 10-K, no subsidiary
  edges, no traversal questions.
- **At least three companies**, one pair with overlapping Exhibit 21 lists.
- **At least one company across three or more years**, ideally one renamed or acquired.
- **One 10-K/A beside the original it amends**, so `as_filed` versus `as_restated` is
  testable. We have a verified candidate — see v2.
- **At least one subsidiary consolidated into a named reportable segment**, so the longest
  path in the schema has something to run on.

The first four reuse the stress sample already requested from Data, so they are not a new
corpus ask. The fifth is a selection criterion rather than more filings: any company that
folded an acquisition into a named segment during the window satisfies it.

## Format

Your existing shape, unchanged. Three requests:

**1 · `gold.evidence` spanning accessions.** For multi-hop questions it becomes a list of
items on **different accessions**, scored on the share found. `evidence_recall@10` already
works this way — it has just never been pointed at cross-document evidence.

**2 · A `hops` field, or `tier: "multihop"`, on every question — not only the new ones.**
This reads like a nicety and is not. It is the x-axis for two of our experiments: comparing
retrievers by question type, and choosing a default for `max_hops`. Without it there is one
average and the multi-hop result disappears into it. Labelling only the tranche does not
work either — the lookup baseline has to be sliceable from the same field. This is the one
ask that costs you work beyond writing questions, so tell us if it should be ours.

**3 · Keep scoring on `contains`, and normalise whitespace both sides.** Evidence matching
is already what runs, and `retrieval_api.md` §10 says gold labels survive re-chunking only
through it. Keep XPath as a human audit trail. Separately: gold quotations collapse
whitespace and rendered HTML does not, so a `contains` match has to collapse runs and trim
ends or exact matches fail for cosmetic reasons.

## What we are not asking for

- Not a change to your metrics, your harness, or `RetrievedChunk`
- Not a second gold-set file or a second format
- Not for Eval to learn our schema — evidence stays `contains` strings and metadata filters
