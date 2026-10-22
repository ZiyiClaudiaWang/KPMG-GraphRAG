# What GraphRAG needs from Eval

Written 2026-10-22 after reading Gold Set v1. For Yulei Ding, Thomas Song and the
Eval/RAG side.

## The ask

We'd like to request adding **15–20 questions added inside yours, in your format**, covering the one shape no
existing set reaches: a question whose answer requires following a relationship to
something the question could not name. No "which subsidiaries", no "after the acquisition",
no entity that has to be resolved before it can be retrieved.

We **will not** write a second gold set. Because a parallel benchmark in our own format would risk
splitting the harness, make scores incomparable, and leave us grading our own arm.


**We propose the shapes and the graph path each should exercise. Eval writes, validates and
owns the wording and the gold evidence.** We should not author the benchmark our own arm is
graded on — if the graph scores well on questions we wrote, nobody outside the team will
believe it.

## What Gold Set v1 settles

v1 is a real improvement on both earlier sets! Good work! It answers two of our three abstention asks.

- **Abstention is better than we asked for.** Ten controls, not the three we wanted —
  EV1-064 and EV1-065 cover M16 and M15 as written.
- **The multi-accession join already exists.** EV1-037 through 039 derive Q4 FY2026 by
  joining the FY2026 10-K to the Q3 10-Q. Two accessions, one answer, scored today. The
  mechanism is built; it has only ever been pointed at periods, never relationships.
- **The fact table stays.** 53 of 100 questions are numeric or comparative, against the
  roughly one-third threshold in our architecture note. On the record before INT-0.

## The gap

Seems like none of the eval v1's set of 100 requires traversal. The cross-company questions are fan-out — independent
lookups with a ticker filter, compared afterwards — which a tuned vector index does well
without a graph. So if round 1 runs against v1 as it stands, the graph arm scores on work
vector already does, and we finish with no evidence the graph earned its place. That is a
coverage gap, not a criticism.

Two risks for GraphRAG:

**Nothing in v1 needs more than two hops.** The deepest question joins two filings on a
period boundary, which leaves `max_hops` — a tool-contract parameter somebody has to pick a
default for — undecidable from the benchmark.

**All ten abstention questions test a missing fact, none a fabricated relationship.** That
is the failure mode specific to a graph. A graph that invents edges answers every other
question in v1 correctly and is caught only by M14.

## Three corpora, not two

Three are now in play, overlapping by ticker but not by filing.

| | this document assumed | GraphRAG pilot, [pilot-filings.md](./pilot-filings.md) | Eval Gold Set v1 |
|---|---|---|---|
| Companies | JPM, BAC, C, CAT, DE, HON | MSFT, JPM, AAPL, ADM | MSFT, AAPL, AMZN, META, NVDA |
| Forms | mixed | 10-K, plus one 10-K/A pair | mostly 10-Q, plus MSFT FY2026 10-K |
| Exhibit 21 reachable | — | yes, all three 10-Ks | MSFT FY2026 only |

The pilot holds Microsoft's and Apple's FY2025 10-Ks; v1 uses Microsoft's FY2026 10-K and
Apple's Q2 FY2026 10-Q. Merged, that gives two consecutive Microsoft annual reports, three
Exhibit 21s and an amendment pair.

**Merging them is the decision that unblocks the rest.** Unmerged, no question needing
Exhibit 21 can be written at all, because v1 reaches one 10-K.

## The nineteen

Shapes and intent, not final wording. Eleven are writable against the merged corpus today.
`X` and `Y` are two companies with overlapping Exhibit 21 lists, `Z` a segment, `S` a
subsidiary named in X's Exhibit 21.

| # | Question | Status, and where it runs |
|---|---|---|
| M01 | Which subsidiaries does X list in Exhibit 21, and how many are incorporated outside the United States? | **Writable** — MSFT FY2025 Exhibit 21. One accession, but the answer aggregates a list rather than retrieving a sentence |
| M02 | Do X and Y have subsidiaries incorporated in the same jurisdiction? | **Writable** — MSFT FY2025 Exhibit 21 and AAPL FY2025 Exhibit 21.1. Two accessions, one join |
| M03 | Which of X's listed subsidiaries also file under their own CIK? | **Writable** — JPM FY2025 Exhibit 21 plus a registrant lookup. JPM because bank subsidiaries do file separately. Drop it if the lookup is outside the corpus boundary |
| M04 | How did X's reportable segments change between two fiscal years? | **Writable** — MSFT FY2025 and FY2026 10-Ks |
| M06 | Which risk factors appear in X's later Item 1A that were absent in the earlier one? | **Writable** — MSFT FY2025 and FY2026 Item 1A. Set difference, not retrieval |
| M09 | How did X's numbers move before and after a corporate event? | **Writable** — Q3 FY2026 10-Q, both comparative columns. No acquisition in the corpus, but the OpenAI recapitalisation is an event with before-and-after numbers and exercises the same path |
| M12 | What did X report as FY2023 revenue as filed, and what did the 10-K/A restate it to? | **Writable** — ADM FY2023 10-K and 10-K/A. Answer already verified, see below |
| M13 | Which figures changed between X's 10-K and its 10-K/A? | **Writable** — same pair, Note 17 |
| M14 | Does X own Y? *(a pair with no ownership relationship)* | **Writable** — ask whether a subsidiary named in Apple's Exhibit 21 is Microsoft's. The name is in the corpus, so retrieval surfaces it; the answer must not attach it to the wrong parent |
| M18 | Which risk factor do X and Y both disclose, and what sentence does each use? | **Writable** — MSFT FY2026 and AAPL FY2025 Item 1A. Five hops each side, meeting at a risk factor neither question names |
| M19 | Which of X's segments were affected by the figures its 10-K/A restated? | **Writable** — ADM FY2023 pair. Note 17 scopes the correction to the Nutrition segment, so the answer is already verified |
| M15b | Which subsidiaries did X list in a year whose 10-K is outside the corpus? | **Bonus** — Microsoft FY2022. The traversal counterpart to EV1-065, which only tests a missing period |
| M05 | In which fiscal year did X first report segment Z? | **Blocked** — needs one more accession, MSFT FY2024 10-K. Also strengthens M04 and M06 |
| M07 | What was X called before it renamed, and what did it report under the earlier name? | **Blocked** — no renamed registrant in any corpus. Eval or Data to nominate |
| M08 | After X acquired Y, in which filing did Y stop appearing as a separate registrant? | **Blocked** — same |
| M17 | Which segment absorbed S after X acquired it, and how did that segment's revenue move? | **Blocked** — needs a subsidiary folded into a named segment. Microsoft and Activision Blizzard is the obvious candidate, **but nobody has checked** that Activision is in Microsoft's Exhibit 21 or that the segment note attributes it. Four hops, the deepest question here — worth half an hour before giving up |
| M10 | Compare segment operating margin for X and Y in the same fiscal year. | **Partial** — EV1-055 and EV1-060 do this at company level. The segment-level version needs two segment-reporting companies, and comparing margins across banking and software is a strange thing to grade. Low priority |
| M11 | Which of X's segments contributed most to revenue growth? | **Met** — GSv0-MSFT-024 |
| M15, M16 | Period, and company, outside the corpus | **Met** — EV1-065 and EV1-064 |

M07 and M08 are the two to drop first if the corpus cannot grow. M05 is one accession.

**The ADM pair comes with its answers checked.** Nutrition intersegment revenues for FY2023
move from USD 114 million as reported to USD 48 million restated, both tagged
`us-gaap:Revenues`, and the amendment says the consolidated statements were unaffected — so
M13's scope is the segment disclosures alone. One trap for the gold note: the original
encodes the elimination negative and the amendment presents it positive, so matching on
concept and period alone pairs the wrong two numbers.

**M14 matters more than the rest.** It is the only question that catches a graph inventing
an edge. Worth agreeing alongside it: a **provenance-correctness sample** — for N answers,
does the cited evidence actually support the claim? An invented relationship still carries
a valid key and a resolvable accession, so the citation renders and the edge is wrong.
Nothing in `evidence_recall` catches that.

## What the corpus has to contain

- **10-Ks, not only 10-Qs.** Exhibit 21 is filed with the 10-K.
- **At least three companies**, one pair with overlapping Exhibit 21 lists.
- **At least one company across three or more years**, ideally one renamed or acquired.
- **One 10-K/A beside the original it amends.** We have a verified candidate, above.
- **At least one subsidiary consolidated into a named reportable segment**, so the longest
  path in the schema has something to run on.

The first four reuse the stress sample already requested from Data, so they are not a new
corpus ask. The fifth is a selection criterion rather than more filings.

## Format

Your existing shape, unchanged. Three requests:

**1 · `gold.evidence` spanning accessions, not just an expected answer.** v1 carries one
scalar per question, computed from XBRL, which works for the other 100. It does not work
here: an answer that happens to be right from a single filing should not score the same as
one that made the join. `evidence_recall@10` already scores on the share of evidence found.

**2 · A `hops` field, or `tier: "multihop"`, on every question — not only the new ones.**
Your `deep` tier cannot stand in for it: it marks reasoning effort, not documents crossed.
EV1-037 is `deep` across two filings, GSv0-MSFT-030 is `deep` across none. The field is the
x-axis for two of our experiments — comparing retrievers by question type, and choosing a
default for `max_hops`. Labelling only the tranche does not work either, because the lookup
baseline has to be sliceable from the same field. This is the one ask that costs you work
beyond writing questions, and v1 more than doubled it — 42 questions when we drafted this,
100 now. Tell us if it should be ours.

**3 · Keep scoring on `contains`, and normalise whitespace both sides.** Evidence matching
is already what runs, and `retrieval_api.md` §10 says gold labels survive re-chunking only
through it. Keep XPath as a human audit trail. Gold quotations collapse whitespace and
rendered HTML does not, so a match has to collapse runs and trim ends or exact matches fail
for cosmetic reasons.

Still open from [eval-reply.md](./eval-reply.md) §2 and sharper now: citation scoring has to
join on the provenance key, not `chunk_id`. Ours is always null, so a traversal answer built
entirely from the graph scores as uncited rather than scoring badly.

## What we are not asking for

- Not a change to your metrics, your harness, or `RetrievedChunk`
- Not a second gold-set file or a second format
- Not for Eval to learn our schema — evidence stays `contains` strings and metadata filters
- Not for Eval to drop or rework any of the 100

Small thing: EV1-052's tier reads `basicZ`. Likely a typo.
