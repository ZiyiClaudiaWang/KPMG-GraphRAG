# Hybrid retrieval: scope

**Owner:** Jacob · **Status:** draft for the freeze decision · **Date:** 22 November 2026
**Tracks:** #105 · **Card:** Hybrid retrieval, `docs/sprints/2026-11-16-acceptance.md`

**Decision asked of the co-leads:** does `hybrid_search` ship by 30 November, or is it
written down and cut? Section 5 lists exactly what would ship.

---

## 1. Which arms, and where the merge happens

**Arms:** `structured_search` and `graph_search`. RAG's `retrieve()` joins as a third
source only if its built index is in hand by 23 November (section 5). It takes the same
filter keys we do (`ticker`, `fiscal_year`, `fiscal_period`, `accession_number`), so
adding it changes no filter logic.

**Where:** in the retrieval layer, as a new entry point beside `graph_search` and
`structured_search` in `retrieve.py`. Not inside an arm, and not in the Agent:

- **Not in an arm.** Each arm stays one source, so Eval's per-arm scores stay meaningful.
- **Not in the Agent.** The Agent orchestrator decides *which* tools to call (#79 took
  routing off the Retrieval card). Merging evidence once retrieved is retrieval's job: the
  Agent gets one ranked, cited list instead of reconciling scales it cannot compare
  (`agent-notes.md`: scores across arms "mean nothing to each other").

**How:**

1. Read `ticker`, `fiscal_year` and `fiscal_period` from the question. The arms already
   apply them per filer (#89, #90, #93); this step only finds them in the wording.
2. Call each arm with the same filters.
3. Merge by **rank, not score** (reciprocal rank fusion, k = 60, the constant RAG
   already uses), keeping at least two rows from each arm so an exact figure is not
   pushed out by prose.
4. Drop any row whose period is outside the filtered window.

The graph arm reaches claims by full-text search only and does not walk `OWNS` or
`CONCERNS_RISK` (`docs/neo4j-instances.md`). Chaining graph results into structured
filters therefore waits for traversal (#95), and is not in this scope.

## 2. A question it answers that neither arm answers alone

<!-- JACOB: write this section.
Facts to work from (Eval's round-1 benchmark, 155 questions):
- 4 are routed xbrl+text: EV1-016, EV1-032, EV1-036, EV1-042. All MSFT, FY2026 Q3.
- EV1-036 is the clearest: "Microsoft Cloud revenue is disclosed in the filing's text.
  What share of total revenue did it represent in the three months ended March 31, 2026?"
  Total revenue is a tagged fact (structured arm); Microsoft Cloud revenue is only in prose.
- Say what each arm returns alone, and why that is not enough.
- Note these 4 already have Eval's gold answers, so hybrid is scored on questions we did not write.
- Be honest about scale: 4 of 155 is a narrow target. Say why it is still worth it, or not.
-->

## 3. The one-provenance-key rule

**Rows are never merged.** A hybrid result is a list of rows from different arms, and
each row keeps the single provenance key its arm already returns:

| Row from | Provenance key |
|---|---|
| Structured | `metadata.fact_id` |
| Graph | `section_id` · `accession_number` |
| RAG (if included) | `chunk_id` · `accession_number` |

Hybrid adds only metadata that leaves those keys untouched: `metadata.source_arm`, and
each row's rank within its own arm. Eval's citation scoring, which joins on the
provenance key, works unchanged. A composed answer that uses a number and a sentence
cites two rows, each with its own key.

## 4. A fourth tool, not a mode

`hybrid_search` is a separate entry point, for #67's reason: *"a flag would mean one name
covering two systems."* If hybrid were a mode on `graph_search`, Eval's graph scores would
silently mix two behaviours. A separate name keeps every arm's numbers clean, and
`metadata.retrieval_method = "hybrid_rrf"` marks each row.

## 5. What ships by 30 November, and what does not

**Ships, if approved:**

- Reading ticker and fiscal period from the question, with tests
- `hybrid_search` over the structured and graph arms: shared filters, rank fusion with a
  per-arm minimum, period guard, contract tests
- A results table on Eval's 4 `xbrl+text` questions, plus single-arm controls, comparing
  hybrid against each arm alone, including where hybrid loses

**Does not ship:**

| Item | Why | Who is affected |
|---|---|---|
| Graph → structured chaining | Needs traversal (#95) | Nobody yet; no consumer depends on it |
| Re-ranking with a cross-encoder | Not needed to show the merge works | Nobody |
| Section pointers on facts (#40) | Separate loading decision | Citation detail only |
| RAG as a third source, if no index by 23 Nov | Index is built locally, not shared | Hybrid runs on two arms |

**Nothing depends on hybrid shipping.** If it is cut, the Agent keeps calling the arms
separately, as it does today.

**Cut lines:**

| Check on | If | Then |
|---|---|---|
| 23 Nov | No RAG index | Two arms only |
| 25 Nov | Behind schedule | Drop the per-arm minimum; plain rank fusion |
| 27 Nov | No gain over the best single arm on the 4 questions | Ship the filter reading alone; record the result |

26 November is Thanksgiving. No review is assumed that day.
