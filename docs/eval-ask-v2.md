# Gold Set v1 review, and the tranche we still need

Drafted 2026-10-22, after reading Eval's Gold Set v1 (100 questions, generated from
`data/benchmark_v1.jsonl`). For Yulei Ding and the Eval/RAG side.

Supersedes the corpus assumptions and the candidate-question wording in
[eval-ask.md](./eval-ask.md). The M01–M19 labels are unchanged, so anything already
written against those numbers still lines up.

Read with [eval-ask-cover.md](./eval-ask-cover.md), which is still unsent and whose
question counts this review changes.

## Short version

Gold Set v1 is a real improvement on both earlier sets, and it answers two of our three
abstention asks without being asked. It contains **no traversal question** — none of the
100 requires following a relationship to something the question could not name.

We are still asking for one tranche inside their set, in their format. What changed is
that we can now name the filings each question runs against, because the gap is no longer
"we need a corpus" — it is that three different corpora are in play and nobody has merged
them.

## What Gold Set v1 settles

**Abstention is better than we asked for.** We asked for three controls; the set has ten —
seven unanswerable and three false-premise. EV1-064 (company outside the corpus) and
EV1-065 (period outside the snapshot) cover M16 and M15 as written.

**The multi-accession join already exists.** EV1-037, EV1-038 and EV1-039 derive Q4 FY2026
by joining the FY2026 10-K to the Q3 FY2026 10-Q. Two accessions, one answer, scored
today. The mechanism we need is built — it has only ever been pointed at periods, never at
relationships.

**The quantitative split settles the fact table.** 53 of 100 questions are numeric or
comparative. Our architecture note said to drop a separate fact table if numeric and
comparative work came in under about a third of real demand. It is over half. The fact
table stays, and that is now on the record before INT-0.

**M11 is met.** GSv0-MSFT-024 reconciles segment revenue changes to the consolidated total
and asks for one segment's share of the increase. That is the question we proposed, within
one filing.

## The gap

| # | Shape | In Gold Set v1 |
|---|---|---|
| M01 | Exhibit 21 subsidiaries by jurisdiction | ✗ |
| M02 | Shared jurisdiction across two Exhibit 21s | ✗ |
| M03 | Subsidiary that files under its own CIK | ✗ |
| M04 | Reportable segments change across years | ✗ |
| M05 | First fiscal year a segment was reported | ✗ |
| M06 | Risk factors present in one year, absent in another | ✗ |
| M07 | Company reporting under two legal names | ✗ |
| M08 | Acquired registrant stops filing | ✗ |
| M09 | Numbers before and after a corporate event | ✗ |
| M10 | Segment margin compared across two companies | partial — EV1-055 and EV1-060 do this at company level, not segment level |
| M11 | Segment contribution to revenue growth | ✓ GSv0-MSFT-024 |
| M12 | As-filed versus restated | ✗ |
| M13 | Which figures changed in an amendment | ✗ |
| M14 | Ownership edge that does not exist | ✗ |
| M15 | Period outside the corpus | ✓ EV1-065 |
| M16 | Company outside the corpus | ✓ EV1-064 |
| M17 | Subsidiary to segment to number, four hops | ✗ |
| M18 | Risk factor two companies share, five hops each side | ✗ |
| M19 | Which segments the restated figures touch | ✗ |

Three met, one partial, fifteen absent.

Nothing in Gold Set v1 needs more than two hops. The deepest question in it is the Q4
derivation, which joins two filings of the same company on a period boundary. That leaves
`max_hops` in the tool contract still undecidable from the benchmark.

The ten abstention questions all test a **missing fact**. None tests a **fabricated
relationship**, which is the failure mode specific to a graph arm. A graph that invents
edges answers every other question in the set correctly and is caught only by M14.

## Three corpora, not two

Our earlier ask argued from `eval/gold_v0.jsonl` and asked which of two corpora was real.
There are now three, and they overlap by ticker but not by filing.

| | eval-ask.md assumed | GraphRAG pilot, [pilot-filings.md](./pilot-filings.md) | Eval Gold Set v1 |
|---|---|---|---|
| Companies | JPM, BAC, C, CAT, DE, HON | MSFT, JPM, AAPL, ADM | MSFT, AAPL, AMZN, META, NVDA |
| Forms | mixed | 10-K, plus one 10-K/A pair | mostly 10-Q, plus MSFT FY2026 10-K |
| Exhibit 21 reachable | — | yes, all three 10-Ks | MSFT FY2026 only |

MSFT and AAPL appear in two of the three, but through different filings. The pilot holds
Microsoft's FY2025 10-K and Apple's FY2025 10-K; Gold Set v1 uses Microsoft's FY2026 10-K
and Apple's Q2 FY2026 10-Q.

That is useful rather than awkward. Microsoft's FY2025 and FY2026 10-Ks sitting in one
corpus give us two consecutive annual reports for the same company, which is what M04 and
M06 need.

Merging these three is the decision that unblocks most of what follows. It is the same
question we raised before, now with the options named.

## The tranche, re-pointed

Shapes and intent, not final wording. Eval owns the wording and the gold evidence; we
should not author the benchmark our own arm is graded on. Each row names the filings the
question has to join.

### Satisfiable against the merged corpus today

| # | Question shape | Filings it must join |
|---|---|---|
| M01 | Which subsidiaries does Microsoft list in Exhibit 21, and how many are incorporated outside the United States? | MSFT FY2025 10-K, Exhibit 21 — one accession, but the answer is an aggregation over a list, not a retrieved sentence |
| M02 | Do Microsoft and Apple list any subsidiaries incorporated in the same jurisdiction? | MSFT FY2025 Exhibit 21 and AAPL FY2025 Exhibit 21.1 — two accessions, one join |
| M03 | Which of JPMorgan Chase's listed subsidiaries also file with the SEC under their own CIK? | JPM FY2025 10-K Exhibit 21, plus a registrant lookup. JPM rather than Microsoft because bank subsidiaries do file separately. Drop this one if the registrant lookup is outside the corpus boundary |
| M04 | How did Microsoft's reportable segments and their composition change between FY2025 and FY2026? | MSFT FY2025 and FY2026 10-Ks |
| M06 | Which risk factors appear in Microsoft's FY2026 Item 1A that were absent from FY2025? | MSFT FY2025 and FY2026 10-Ks — set difference, not retrieval |
| M09 | How did the reported effect of the OpenAI investment on net income change between the nine months ended 31 March 2025 and 2026? | Q3 FY2026 10-Q, both comparative columns. Re-pointed: the corpus has no acquisition, but the recapitalisation is an event with before-and-after numbers, which exercises the same path |
| M12 | What did ADM report as FY2023 Nutrition intersegment revenues as filed, and what did the 10-K/A restate it to? | ADM FY2023 10-K and 10-K/A |
| M13 | Which figures changed between ADM's FY2023 10-K and its 10-K/A? | Same pair, Note 17 |
| M14 | Is *(a subsidiary named in Apple's Exhibit 21)* a subsidiary of Microsoft? | Both Exhibit 21s. The name is in the corpus, so retrieval will surface it; the answer must not attach it to the wrong parent |
| M15b | Which subsidiaries did Microsoft list in its FY2022 Exhibit 21? | None — FY2022 is outside the corpus. The traversal counterpart to EV1-065, which only tests a missing period |
| M18 | Which risk factor do Microsoft and Apple both disclose, and what sentence does each use? | MSFT FY2026 and AAPL FY2025 Item 1A. The shared risk is what they have in common, not the wording, so retrieving the most similar passages from each does not answer it |
| M19 | Which of ADM's reportable segments were affected by the figures its 10-K/A restated? | ADM FY2023 10-K and 10-K/A. Note 17 scopes the correction to the Nutrition segment and the intersegment eliminations, so the answer is already verified |

Two notes on gold answers:

**M12 and M13 have a verified answer already.** Our pilot manifest records it: Nutrition
intersegment revenues for FY2023 move from USD 114 million as reported to USD 48 million
restated, both tagged `us-gaap:Revenues`. The amendment states the consolidated statements
were not affected, so the correct scope for M13 is the segment disclosures alone.

**M12 carries a scoring trap worth writing into the gold note.** The original encodes the
elimination with a negative sign and the amendment presents the restated amount positive.
Matching facts by concept and period alone will pair the wrong two numbers. Signs and
presentation context have to survive.

### Blocked until the corpus grows

| # | What it needs | Cost |
|---|---|---|
| M05 | A third consecutive Microsoft 10-K — FY2024 would do it, and it strengthens M04 and M06 at the same time | One accession |
| M07 | A registrant that changed its legal name | We have no verified candidate; Eval or Data to nominate |
| M08 | An acquired registrant whose filings stop | Same |
| M17 | A subsidiary folded into a named reportable segment during the window. Microsoft and Activision Blizzard is the obvious candidate given what is already in the corpus, but we have not checked that Activision appears in Microsoft's Exhibit 21 or that the segment note attributes it — somebody should confirm before this is written | Possibly none, if the candidate holds |

M07 and M08 are the two we would drop first if the corpus cannot grow. M05 is cheap enough
that it probably should not be dropped. M17 is the deepest question in the set, so it is
worth half an hour of checking before giving up on it.

### Already met

M10 is substantially covered at company level by EV1-055 and EV1-060. The segment-level
version needs two segment-reporting companies in one corpus, which Microsoft and JPMorgan
would give us, but comparing margins across banking and software is a strange question to
grade. Low priority.

M11, M15 and M16 need nothing.

## Format

Their existing shape, unchanged. Three requests:

**A `hops` count, or a `tier: "multihop"` label, on every question — not only the new ones.**
Gold Set v1 tiers are `basic`, `comparative` and `deep`, and `deep` marks reasoning effort
rather than how many documents the answer crosses. EV1-037 is `deep` and crosses two
filings; GSv0-MSFT-030 is `deep` and crosses none. So the existing field cannot separate
them.

This is the one ask that costs Eval work beyond writing questions, and the cost went up:
the covering note offered to label 42 questions and there are now 100. Tell us if it should
be ours to do.

It is not a nicety. It is the x-axis for two of our experiments — comparing retrievers by
question type, and picking a default for `max_hops` in the tool contract. Labelling only
the new tranche does not work either, because the lookup baseline has to be sliceable from
the same field.

**Multi-hop rows need `gold.evidence` spanning distinct accessions, not only an expected
answer.** Gold Set v1 carries one scalar per question, computed from XBRL. That works well
for the other 100. It does not work here: an answer that happens to be right from a single
filing should not score the same as one that actually made the join. The evidence list is
what distinguishes them, and `evidence_recall@10` already scores that way.

**Keep scoring on `contains`, and normalise whitespace on both sides.** Zero of the earlier
JSONL questions had `chunk_ids` populated, so evidence matching is already what runs. The
gold quotations collapse whitespace and rendered HTML will not, so any `contains` match has
to collapse runs and trim ends or exact matches fail for cosmetic reasons.

Also still open from [eval-reply.md](./eval-reply.md) §2, and it matters more now: citation
scoring has to join on the provenance key rather than `chunk_id`. Every result from both
our tools has `chunk_id = None`. A traversal question answered entirely from the graph
would score as uncited rather than scoring badly.

## Two small things

EV1-052's tier reads `basicZ`. Likely a typo.

Our action list still shows [eval-ask.md](./eval-ask.md) as unsent, and Gold Set v1 credits
only RAG's Gold Set v0 and Eval's own additions. We should confirm whether the original ask
ever reached Eval before sending this, so that it reads as a follow-up rather than as a
complaint about being ignored.

## What we are not asking for

- Not a change to their metrics, their harness, or `RetrievedChunk`
- Not a second gold-set file or a second format
- Not for Eval to learn our schema — evidence stays expressed as `contains` strings and
  metadata filters
- Not for Eval to drop or rework any of the 100
