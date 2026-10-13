# What GraphRAG needs from Eval

Draft, 2026-10-13. For Yulei Ding and the Eval/RAG side.

## Short version

We are **not** writing a second gold set. RAG already has a working one — `gold_set_v0.md` plus `eval/gold_v0.jsonl` plus an evidence-matching scorer in `rag/evaluate.py`. A parallel benchmark in our own format would split the harness, make scores incomparable, and leave us grading our own arm.

We want **one more tranche inside their set, in their format**, covering the question types the current set cannot reach.

## Why a tranche is needed

`gold_set_v0.md` is 30 questions over a **single Microsoft 10-Q**. The file says so plainly: "There is no cross-company or cross-document coverage yet."

Every question is answerable from one filing. None requires traversal. So the current set cannot distinguish the graph arm from a well-tuned vector index — if Eval round 1 runs at W8 against this set, the graph scores on questions vector already answers, and we finish the project with no evidence the graph earned its place.

This is a coverage gap in the benchmark, not a criticism of the set. The set is good at what it covers, and its conventions are sound.

## What we are asking for

**15–20 questions requiring more than one filing.** Four shapes, each unanswerable without traversal or cross-document joins:

| Shape | Example | Why vector can't do it |
|---|---|---|
| Subsidiary traversal | "Which subsidiaries does X list, and which of them file separately?" | The relationship is in Ex-21, the answer needs a join to another filing |
| Same company across years | "How did X's reported segments change between FY2021 and FY2023?" | Requires aligning three filings and noticing a rename |
| Entity resolution | "What did X report before and after it was acquired?" | The company has two names and one identity |
| Numeric + relational | "How did R&D spend change after the 2023 acquisition?" | Graph resolves the event, structured supplies the numbers |

**Requirements that follow from those shapes:**

- **10-Ks, not only 10-Qs.** Ex-21 is filed with the 10-K. No 10-K, no subsidiary edges, no traversal questions.
- **At least three companies**, with at least one pair sharing overlapping Ex-21 subsidiary lists.
- **At least one company across three or more years**, and ideally one that renamed or was acquired.
- **One 10-K/A beside the original it amends**, so the as-filed vs as-restated distinction is testable.

That is the same stress sample already requested from Data in the data contract, so this does not add a new corpus ask — it reuses one that is already outstanding.

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

We would also like a `hops` or `tier: "multihop"` label so graph-requiring questions can be reported as their own slice. Without it the multi-hop result disappears into an average.

## Three smaller things

**1 · Scoring should stay on evidence matching, not XPath.** The markdown gold set locates evidence by `Original HTML XPath` into the pinned EDGAR HTML, explicitly "not cleaned-text offsets". That is a third coordinate system — we cite normalized-document offsets and UI highlights by quote. Nobody owns the XPath-to-section mapping.

The fix is already in their contract: `retrieval_api.md` §10 grades by `contains` strings when gold chunk IDs are absent, and says gold labels survive re-chunking only through evidence matching. So **keep XPath as a human audit trail and score on `contains`.** Nothing new to build.

**2 · Whitespace normalisation needs stating once.** The gold set collapses whitespace in its evidence quotations; rendered HTML will not. Any `contains` match must normalise whitespace on both sides — collapse runs, trim ends — or exact matches fail for cosmetic reasons.

**3 · The quantitative split confirms a decision on our side.** 10 of 30 questions are quantitative and 10 are comparative. Our architecture note said to abandon a separate fact table if numeric and comparative work fell below about a third of real demand. It is over half, so the fact table stays. Useful to have that on the record before INT-0.

## What we are not asking for

- Not a change to their metrics, their harness, or `RetrievedChunk`
- Not a second gold-set file or a second format
- Not for Eval to learn our schema — evidence stays expressed as `contains` strings and metadata filters
