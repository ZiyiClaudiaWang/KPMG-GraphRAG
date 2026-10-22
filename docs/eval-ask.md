# What GraphRAG needs from Eval

Draft, 2026-10-13. For Yulei Ding and the Eval/RAG side.

> **Partly superseded on 2026-10-22 by [eval-ask-v2.md](./eval-ask-v2.md).** Eval has since
> published Gold Set v1, which meets M11, M15 and M16 and settles the quantitative-split
> question. The corpus this document argues from is also no longer the live one. The
> argument for a traversal tranche still stands and the M01–M19 labels are unchanged; read
> v2 for the current corpus and the re-pointed questions.

## Short version

We are **not** writing a second gold set. RAG already has a working one — `gold_set_v0.md` plus `eval/gold_v0.jsonl` plus an evidence-matching scorer in `rag/evaluate.py`. A parallel benchmark in our own format would split the harness, make scores incomparable, and leave us grading our own arm.

We want **one more tranche inside their set, in their format**, covering the question types the current set cannot reach.

## Why a tranche is needed

There are two gold sets, and neither contains a traversal question.

| | `gold_set_v0.md` | `eval/gold_v0.jsonl` |
|---|---|---|
| Questions | 30 | 12 |
| Companies | MSFT only | JPM, BAC, C, CAT, DE, HON |
| Cross-company questions | none | 4 |
| `chunk_ids` populated | — | 0 of 12 |
| Status | draft, no human review | all 12 draft |

The markdown set says so itself: "There is no cross-document coverage yet." The JSONL set does have four cross-company questions — C01 and C03 compare three banks, C02 and C04 compare three industrials.

But those four are **fan-out, not traversal**. Each is three independent lookups with a ticker filter, compared afterwards. A vector index with metadata filtering does that well, and does not need a graph to do it.

What neither set has is a question where the answer requires following a relationship to something you could not name in the query. No "which subsidiaries", no "after the acquisition", no entity that must be resolved before it can be retrieved. That is the whole argument for the graph arm, and it is currently unmeasured.

So if Eval round 1 runs at W8 against either set, the graph scores on work a tuned vector index already does, and we finish the project with no evidence the graph earned its place.

This is a coverage gap, not a criticism. Both sets are good at what they cover.

**One thing to settle alongside it:** the two sets imply two different pilot corpora, and there is a `pilot-corpus-setup` branch as well. Which corpus is real? Our stress-sample request to Data should match it — and if the pilot is six companies across banking and industrials, the overlapping-Exhibit-21 requirement may already be satisfiable without asking for anything new.

## What we are asking for

**15–20 questions requiring more than one filing.** Five shapes, each unanswerable without traversal or cross-document joins:

| Shape | Example | Why vector can't do it |
|---|---|---|
| Subsidiary traversal | "Which subsidiaries does X list, and which of them file separately?" | The relationship is in Ex-21, the answer needs a join to another filing |
| Same company across years | "How did X's reported segments change between FY2021 and FY2023?" | Requires aligning three filings and noticing a rename |
| Entity resolution | "What did X report before and after it was acquired?" | The company has two names and one identity |
| Numeric + relational | "How did R&D spend change after the 2023 acquisition?" | Graph resolves the event, structured supplies the numbers |
| Deep traversal, three hops or more | "Which segment absorbed subsidiary S, and how did that segment's revenue move?" | Added 20 Oct — see below. No single hop gets from the subsidiary to the number |

**Requirements that follow from those shapes:**

- **10-Ks, not only 10-Qs.** Ex-21 is filed with the 10-K. No 10-K, no subsidiary edges, no traversal questions.
- **At least three companies**, with at least one pair sharing overlapping Ex-21 subsidiary lists.
- **At least one company across three or more years**, and ideally one that renamed or was acquired.
- **One 10-K/A beside the original it amends**, so the `as_filed` vs `as_restated` distinction is testable.
- **At least one subsidiary consolidated into a named reportable segment**, so the longest path in the schema has something to run on.

The first four are the same stress sample already requested from Data in the data contract, so they do not add a new corpus ask — they reuse one that is already outstanding. The fifth is new, and it is a selection criterion rather than more filings: any company that acquired a business and folded it into a named segment during the window satisfies it.

## How we propose to split the work

We propose the question **shapes** and supply candidate questions with the graph paths they should exercise. Eval writes, validates and owns the final wording and the gold evidence.

We should not author the benchmark our own arm is graded on. Keeping authorship with Eval protects the result — if the graph scores well on questions we wrote, nobody outside the team will believe it.

## Format

Their existing shape, unchanged — from `eval/gold_v0.jsonl`:

```json
{
  "qid": "B01",
  "tier": "basic",
  "question": "...",
  "filters": {"ticker": "JPM", "form": "10-K"},
  "gold": {"chunk_ids": [], "evidence": [{"ticker": "JPM", "form": "10-K", "contains": ["CET1"]}]},
  "expected": {"ticker": ["JPM"]},
  "quantitative": true,
  "status": "draft",
  "notes": "..."
}
```

Multi-hop questions need one change: `gold.evidence` becomes a list of several items spanning **different accessions**, and the question is scored on the share of evidence items found. Their `evidence_recall@10` already works that way for deep questions, so the metric exists — it just has not been pointed at cross-document evidence yet.

**We need a `hops` field, or a `tier: "multihop"` label, on every question — not only the new ones.** This reads like a nicety and is not. Our retriever comparison reports accuracy *by question type*, and our expansion-depth experiment sweeps one, two and three hops to settle `max_hops` in the tool contract. Both take the label as their x-axis. Without it there is one average, the multi-hop result disappears into it, and the depth decision is made by guess. Labelling only the tranche is not enough either — the lookup baseline has to be sliceable from the same field.

## Three smaller things

**1 · Scoring already runs on evidence matching, and should stay there.** Zero of the 12 JSONL questions have `chunk_ids` populated, so every one of them is already scored by `contains` strings. This is not a proposal, just a request not to switch away from it. The markdown gold set locates evidence by `Original HTML XPath` into the pinned EDGAR HTML, explicitly "not cleaned-text offsets". That is a third coordinate system — we cite normalized-document offsets and UI highlights by quote. Nobody owns the XPath-to-section mapping.

The fix is already in their contract: `retrieval_api.md` §10 grades by `contains` strings when gold chunk IDs are absent, and says gold labels survive re-chunking only through evidence matching. So **keep XPath as a human audit trail and score on `contains`.** Nothing new to build.

**2 · Whitespace normalisation needs stating once.** The gold set collapses whitespace in its evidence quotations; rendered HTML will not. Any `contains` match must normalise whitespace on both sides — collapse runs, trim ends — or exact matches fail for cosmetic reasons.

**3 · The quantitative split confirms a decision on our side.** 10 of 30 questions are quantitative and 10 are comparative. Our architecture note said to abandon a separate fact table if numeric and comparative work fell below about a third of real demand. It is over half, so the fact table stays. Useful to have that on the record before INT-0.

## Candidate questions

Shapes and intent, not final wording — Eval owns the wording and the gold evidence. `X`, `Y` are two companies with overlapping Exhibit 21 lists; `Z` is a segment.

### Subsidiary traversal — graph only

| # | Question | Why vector cannot do it |
|---|---|---|
| M01 | Which subsidiaries does X list in Exhibit 21, and how many are incorporated outside the United States? | Answer requires reading a list and aggregating by jurisdiction |
| M02 | Do X and Y have any subsidiaries incorporated in the same jurisdiction? | Two filings, two exhibits, one join |
| M03 | Which of X's listed subsidiaries also file with the SEC under their own CIK? | Requires resolving a name to a registrant in another filing |

### Same company across years — graph plus temporal

| # | Question | Why |
|---|---|---|
| M04 | How did X's reportable segments change between FY2021 and FY2023? | Three filings aligned on a changing segment set |
| M05 | In which fiscal year did X first report segment Z? | Requires ordering filings, not retrieving one |
| M06 | Which risk factors appear in X's FY2023 10-K that were absent in FY2021? | Set difference across two Item 1As |

### Entity resolution — the fuzzy branch

| # | Question | Why |
|---|---|---|
| M07 | What was X called before FY2022, and what did it report under the earlier name? | One identity, two legal names |
| M08 | After X acquired Y, in which filing did Y stop appearing as a separate registrant? | Requires the acquisition event and a filing-history join |

### Numeric plus relational — graph plus structured

| # | Question | Why |
|---|---|---|
| M09 | How did X's R&D spend change in the two fiscal years after the 2023 acquisition? | Graph resolves the event date, structured supplies the values |
| M10 | Compare segment operating margin for X and Y in the same fiscal year. | Two companies, dimensional facts, a derived ratio |
| M11 | Which of X's segments contributed most to revenue growth between FY2022 and FY2023? | Dimensional facts plus arithmetic, total vs part |

### Amendments and basis — structured plus temporal

| # | Question | Why |
|---|---|---|
| M12 | What did X report as FY2023 revenue as filed, and what did the 10-K/A restate it to? | Both versions must survive ingestion |
| M13 | Which figures changed between X's 10-K and its 10-K/A? | Requires `basis` and `amends_accession` |

### Deep traversal — added 20 Oct, after the ontology merge

M01–M16 were written on 13 Oct. The ontology was reconciled on the 17th, and two
things it settled have no question above: the `Subsidiary -[:CONSOLIDATED_INTO]->
Segment` edge, and the `Section -[:SUPPORTS]-> Claim -[:CONCERNS_RISK]-> RiskFactor`
path, which M06 is currently the only question to touch.

These three also carry the depth. Most of M01–M16 resolves in one or two hops, so a
set without them cannot tell us whether expanding to three hops helps or just adds
context — and `max_hops` is a parameter in the tool contract that somebody has to
pick a default for. `S` is a subsidiary named in X's Exhibit 21.

| # | Question | Why vector cannot do it |
|---|---|---|
| M17 | Which reportable segment absorbed S after X acquired it, and how did that segment's revenue move in the next fiscal year? | Four hops: company to subsidiary to segment to fact. The subsidiary's name never appears in the segment revenue table — the link is in Exhibit 21 and the segment note, the number is in XBRL. No chunk contains both ends |
| M18 | Which risk factor do X and Y both disclose, and what sentence does each use to describe it? | Five hops each side, meeting at a risk factor neither question names. Retrieved separately, two Item 1As return the most similar *passages*; the shared risk is the thing they have in common, not the wording |
| M19 | Which of X's reportable segments were affected by the figures its 10-K/A restated? | Three hops across the amendment. Requires knowing which facts differ between two filings, then which segment scopes each. Segment attribution is a dimension on the fact, not a sentence in the text |

M17 and M19 are also the two questions our restatement and subsidiary work is built
to answer, so leaving them out means our strongest results sit outside the benchmark
and get reported by us about ourselves. That is the thing this whole document is
trying to avoid.

### Abstention controls — the ones that catch invention

These matter more than the rest. A graph that invents edges scores well on every question above and fails only here.

| # | Question | Expected behaviour |
|---|---|---|
| M14 | Does X own Y? *(asked of a pair with no ownership relationship)* | "No evidence", not a fabricated edge |
| M15 | Which subsidiaries did X list in a year whose 10-K is not in the corpus? | Abstain, state the gap |
| M16 | A question about a company absent from the corpus | `[]` and an abstention, not a plausible answer from a similar company |

Also worth agreeing at W4: a **provenance-correctness sample**. For N answers, does the cited evidence actually support the claim? Provenance buys auditability, not accuracy — an invented relationship still carries a valid key and a resolvable accession, so the citation renders and the edge is wrong. Nothing in `evidence_recall` catches that.

## What we are not asking for

- Not a change to their metrics, their harness, or `RetrievedChunk`
- Not a second gold-set file or a second format
- Not for Eval to learn our schema — evidence stays expressed as `contains` strings and metadata filters
