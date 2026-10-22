# GraphRAG Experiment Plan — SEC Filings

Oct 19, 2026 · @Lin. Rescoped 20 Oct against the ontology merged on 17 Oct and the repo as it stands.

## What changed in this revision

The first draft was written against the pre-reconciliation schema. Decisions 2.1 to 2.8 settled on 17 Oct and six things in the starter schema now conflict with them — most importantly `Chunk` as a node, which the whole key scheme exists to avoid. The schema below is ontology v1, not a parallel proposal.

Three other changes. Of the original thirteen (E0–E12): **ten are kept**, **E3 shrinks to one of its four sub-experiments**, **two are dropped outright** (E5, E11), and **three are added** (E13–E15). Thirteen in the plan, every one listed by ID below, nothing disappearing silently. The additions are the two findings the team spent a week settling, plus abstention; they matter more than anything cut. And the harness is now the first deliverable rather than an afterthought, because an experiment nobody can re-run is a slide, not a result.

## Scope and fixed conditions

Thirteen GraphRAG experiments over 10-K and 10-Q filings, built on Neo4j. Extraction stays in-house — Pydantic plus structured outputs plus the plain `neo4j` driver, mentions resolved to entities in a separate stage. **`neo4j-graphrag` is used for its retrievers only**, as the comparison arm in E7 and E8. That is not a reversal of the in-house extraction decision and should not be read as one; retrieval library and extraction library are separable, and hand-rolling five retrievers to compare against is wasted work.

Held constant in every experiment, so results compare cleanly with the vector team:

| Condition | State |
| --- | --- |
| Same LLM for extraction and answering | **Ask.** Not agreed with RAG |
| Same question set, split by lookup / multi-hop / temporal / cross-company | **Ask, and dated.** This is the gold set, which freezes in W6 |
| Same chunking as the vector team's best strategy | **Ask.** They may not have picked one |
| Same companies and filings | **Blocked.** Three different Microsoft accession numbers are in play |

These are contract asks on other teams, not givens. All four ride messages already owned by the contracts seat — they are four extra paragraphs, not four new threads. If they do not go out this week, the comparability claim in every experiment below is gone, and the question-set one cannot go out late at all.

## Schema — ontology v1, as merged 17 Oct

```
(:Company {cik, ticker, name})
(:Filing {accession_number, form, period_end, filed_date})
(:Section {section_id, section_key})
(:Subsidiary {sub_id})
(:Segment {segment_id})
(:Fact {fact_id, concept, period_end})        -- pointer only, no value
(:Claim {claim_id, quote, claim_type})
(:RiskFactor {risk_id})

(Company)-[:FILES]->(Filing)
(Filing)-[:AMENDS]->(Filing)
(Filing)-[:CONTAINS]->(Section)-[:CONTAINS]->(Section)
(Filing)-[:REPORTS]->(Fact)
(Company)-[:OWNS]->(Subsidiary)
(Company)-[:HAS_SEGMENT]->(Segment)
(Subsidiary)-[:CONSOLIDATED_INTO]->(Segment)
(Segment)-[:SCOPES]->(Fact)
(Section)-[:SUPPORTS]->(Claim)-[:CONCERNS_RISK]->(RiskFactor)
```

Six corrections against the first draft, each one a settled decision rather than a preference:

| First draft | v1 | Decision |
| --- | --- | --- |
| `(:Chunk)`, `(Chunk)-[:PART_OF]->(Filing)` | **No chunk node.** `source_chunk_id` is an optional attribute and never part of a key | 2.2 |
| `(:Fact {value, unit})` | Values live in DuckDB. The graph holds a pointer | Fact-table split |
| `(:Executive)` | Excluded from v1 | 2.5 |
| `(Company)-[:FILED]->` | `FILES` | Part 0 |
| `(Fact)-[:FOR_SEGMENT]->(Segment)` | `(Segment)-[:SCOPES]->(Fact)` | — |
| `(Filing)-[:DISCLOSES]->(RiskFactor)` | `Section -[:SUPPORTS]-> Claim -[:CONCERNS_RISK]-> RiskFactor` | 2.3, 2.4 |

The chunk row is the one to read twice. Data re-chunks between W6 and W9. A chunk-keyed edge gets a new key on every re-parse, so the same relationship extracted from the same unchanged sentence MERGEs as a second edge and the graph silently doubles. Nine tests in `tests/test_toy_graph_fixture.py` enforce it.

## The harness, first

Before any experiment, not after the first three.

`experiments/` in the repo. One runner, one results row per run, appended to a CSV under version control. Every row carries `extraction_version`, `index_version`, `corpus_version`, the experiment ID, and the four metrics below. Re-run on every graph load.

- Answer accuracy by question type — lookup, multi-hop, temporal, cross-company
- Retrieval precision
- Latency per query
- Cost per query and per filing indexed

This is roughly a day and it is the difference between thirteen experiments and thirteen anecdotes. It also makes every later experiment an afternoon instead of a week, which is the only reason thirteen fit in the time left.

## Phase 0 — no loaded filings required

Four of these start this week. The gate on Phase 0 is not labour and never was; it is that none of them needs a real filing in a graph.

| ID | Experiment | What varies | Output | Rides |
| --- | --- | --- | --- | --- |
| **E0** | Ontology check, run both ways | The 19 candidate questions in `eval-ask.md`, each mapped to a Cypher path — then every experiment below, mapped to the questions that score it | Questions v1 cannot answer → v2 changes. Experiments with no question → a question, or an admission. **Feeds the gold set before it freezes in W6** | Extraction 2's card, moved to W5 |
| **E1** | Retriever plumbing | Five retrievers wired against the schema probe | Working harness for Phase 1 | New — needs a seat |
| **E2** | Text2Cypher baseline | With and without schema hints and few-shot examples | Valid-Cypher rate, failure types | New — needs a seat |
| **E4a** | XBRL loading | Inline XBRL instance → DuckDB, MSFT only | `lookup_fact` returns value, unit, period, dimensions, `fact_id` | Structured's W5 card |

E0 is the urgent one. The gold set freezes in W6, so a question not written by Friday is a question the benchmark never asks — and W8 is where the graph has to show it earned its place.

**Run E0 in both directions.** Mapping questions to Cypher paths finds questions v1 cannot answer. It cannot find an experiment with no question, and on 20 Oct there were three: nothing in M01–M16 reached a segment through a subsidiary, so **E14** had nothing to score it; only M06 touched the `Claim` path that **E6** depends on; and almost every candidate resolved in one or two hops, so **E8**'s depth sweep would have measured question design rather than graph design. M17–M19 close the first and third (PR #16). The reverse check is what catches the next one.

Two things that follow from running it:

- **Sample size.** After the tranche, the multi-hop slice is 15–20 questions, three of which are abstention controls scored pass/fail. E7 compares five retriever arms on the remaining 12–17, where one question is six to eight points. E7 can show categorical failure — vector returns nothing on a traversal — and cannot rank arms that are close. Say which claim we are making before the set freezes, because afterwards it cannot be widened.
- **E10 has no question and probably should not get one.** Sector-wide community questions need 12–15 companies, which is behind the W9 gate. If the gate opens and the gold set froze in W6 without them, E10 is unscoreable. Cheaper to accept that now than to discover it in W10.

E4a is the highest-value *new* work available, for three reasons that have nothing to do with experiments: it has no dependency on Data's credentials, which have been carried for two weeks; it closes the "record the fact-table decision, settled by evidence, still unwritten" card; and without an XBRL loader the restatement experiment in W8 cannot run at all.

## Phase 1 — one company

About three years of filings, roughly twelve documents. Company follows whichever Microsoft accession number wins.

| ID | Experiment | What varies | Metric |
| --- | --- | --- | --- |
| **E4b** | Numeric source | XBRL facts loaded via Cypher / LLM-extracted numbers | Accuracy on 20–30 facts |
| **E6** | Filing linkage | Risk factors tracked across filings | Accuracy on "what is new or changed" questions |
| **E7** | Retriever comparison | Vector / VectorCypher / Hybrid / HybridCypher / Text2Cypher | Accuracy by question type, latency |
| **E8** | Expansion depth | 1 / 2 / 3 hops in the VectorCypher retrieval query | Answer quality vs. context size. Tunes `max_hops`, already in the tool contract |

E6 kept, and cheaper than it looks: it is M06 in `eval-ask.md` — "which risk factors appear in X's FY2023 10-K that were absent in FY2021" — and the `Section -[:SUPPORTS]-> Claim -[:CONCERNS_RISK]-> RiskFactor` structure that answers it is exactly what decisions 2.3 and 2.4 settled. It is a set difference across two Item 1As once two years of one company are loaded.

### Dropped, and why

| ID | Was | Why |
| --- | --- | --- |
| **E3a** | Granularity, ~6 vs ~15–20 node types | Means re-extracting the corpus under a different schema. The ontology froze 17 Oct after a week of reconciliation; proposing this in November re-opens a settled decision at the most expensive moment |
| **E3c** | Fact nodes vs properties | Neither variant is what v1 does. The fact-table split puts values in DuckDB and a pointer in the graph, so as written this cannot validate the decision actually taken |
| **E3d** | Standards alignment, XBRL or FIBO | Not cut so much as relocated — see *Reference data*. Worth doing as a check on v1, not as a competing v1 |
| **E5** | Temporal modeling, derived values and year-over-year | Overlaps E13, which tests the same machinery against a real restatement rather than a derived quantity. Deriving Q4 as annual minus Q1–Q3 also stores a computed value, which `schema-ids-operations.md` Layer 2 forbids for exactly the reason E13 demonstrates |
| **E11** | PageRank or degree for context ranking | A tuning experiment. There will not be time, and nothing downstream depends on it |

**E3b survives, reframed.** Typed edges (`HAS_SEGMENT`, `CONSOLIDATED_INTO`) versus a generic `RELATED_TO {type}`, measured as **Text2Cypher accuracy** rather than as an ontology proposal. That asks whether our naming helps a model write correct queries, which is a live question that does not touch what the nodes are. Runs with E2.

## Phase 2 — the three that are ours

These are not in the original plan and they are worth more to the December readout than the Phase 2 experiments they displace. Each one is a question the vector arm structurally cannot answer, which is the whole thesis.

| ID | Experiment | The question | Why it is the strongest result available |
| --- | --- | --- | --- |
| **E13** | Restatement | ADM FY2023 Nutrition intersegment revenue: $114m as filed, $48m as restated. Which does each arm return? | Vector returns whatever the chunker surfaced. Both numbers are defensible from the filing text and only one is current. This is audit work, in KPMG's own domain |
| **E14** | Subsidiary to segment | Which segment absorbed a named subsidiary, and how did its revenue move? | Finding 1. Vector cannot do the first hop. Needs `CONSOLIDATED_INTO`, settled as 2.8 |
| **E15** | Abstention | The M14–M16 controls | The only questions that catch a graph inventing edges. Nobody else on the project can measure this |

E13 depends on the ADM pair being confirmed and loaded. That is now a dated card.

## Phase 3 — scale, if the gate opens

| ID | Experiment | What varies | Metric |
| --- | --- | --- | --- |
| **E12** | Cost and scaling | 1 → 12–15 companies; adding one new 10-Q | Indexing cost and time, update cost |
| **E9** | Entity resolution | With and without merging on CIK, LEI and name variants | Duplicate rate, cross-company accuracy |
| **E10** | Communities | Leiden plus LLM summaries, by community level | Accuracy on sector-wide questions |

E12 is not really Phase 3 — the W9 scale-up gate depends on the token-cost number before any corpus grows, so the first measurement happens on the Phase 1 company and is already a W5 card. E9 and E10 need 12–15 companies loaded and are honestly one week each at the end. E11 (PageRank context ranking) is cut; it is a tuning experiment and there will not be time.

## Reference data worth using

None of this is ours to build and all of it is free.

| Source | What it gives us | Where it lands |
| --- | --- | --- |
| **GLEIF LEI + Level 2 relationships** | Authoritative direct and ultimate parent records | **Ground truth for the fifty subsidiary pairs and for E9.** Today that answer key is 42 AI-drafted pairs reviewed by one person; a measurement is only as good as its key |
| **SEC DERA Financial Statement Data Sets** | Quarterly dumps of every XBRL fact filed | Independent check on extraction, and a shortcut to the fact table. E4b |
| **US-GAAP taxonomy + DEI** | The vocabulary `Fact.concept` already uses, plus calculation linkbases saying which concepts roll up into which | Structure we are not currently exploiting. E4b, E13 |
| **FIBO, Business Entities module** | Published modelling of legal entities, ownership and control | A check on `Company` / `Subsidiary` / `OWNS`, not a replacement. Far larger than we need. This is where E3d goes |

GLEIF first. It is the cheapest of the four and it fixes a known weakness in a measurement we are about to rely on. File formats and endpoints are unverified as of this revision — check before anyone builds against them.

## Seats

Assigned against the second-hat structure in [assignments.md](./assignments.md), which
pairs every stream and says second hats should peak in different weeks from primaries.

| ID | Experiment | Primary | Second |
| --- | --- | --- | --- |
| E0 | Ontology check | Alina | Kelly |
| E1 | Retriever plumbing | Kelly | Claudia |
| E2 | Text2Cypher baseline | Adeline | Alina |
| E3b | Typed vs generic edges | Alina | Adeline |
| E4a, E4b | XBRL loading, then facts vs LLM | Ashley | Jacob |
| E6 | Risk factors across filings | Claudia | Alina |
| E7 | Retriever comparison | Alison | Claudia |
| E8 | Expansion depth | Claudia | Alison |
| E9 | Entity resolution | Ashley | Alison |
| E10 | Leiden communities | Kelly | Alison |
| E12 | Cost and scaling | Jacob | — |
| E13 | Restatement | Ashley | Claudia |
| E14 | Subsidiary to segment | Claudia | Alison |
| E15 | Abstention | Alison | Alina |
| Harness | — | Jacob | Kelly |

Four of these need saying out loud.

**E1 and E10 sit with the plan-and-finish co-lead, which is off-hat.** That is deliberate.
Both would otherwise land on Retrieval, and `assignments.md` already flags that seat as
seconding eight contract threads while replacing the INT-0 mock. Kelly is working on the
retrieval pipeline this week regardless, so E1 labels work already in flight rather than
adding any. Claudia seconds, because her second hat is retrieval and is scheduled to peak
W6–W7 — exactly when E1 lands.

**E2 is W6 work, not W5.** The fit is good: E2 and E3b ask whether our naming lets a model
write correct Cypher, which is the query-side of the contract seat's existing work. The
sequencing is the problem. In W5 the contracts seat is the only person who can send the
four contract messages, and the question-set one cannot go out late because the gold set
freezes in W6.

**Retrieval is capped at two primaries.** E8 moved to Extraction 1 and E10 to the co-lead
to hold that line. Any further experiment landing on Retrieval needs something removed
first, not added alongside.

**The harness is Platform's.** It is loaders, builds and environments, and it runs in CI,
which that seat already owns.

## One per sprint, and what it rides

Six weeks to the 30 Nov freeze. The sequencing constraint is loaded filings, not seats — but experiments should replace or relabel existing cards rather than stack on top of them, because every seat already runs three to five items and one has flagged overload.

| Week | Experiments | Seat | New work? |
| --- | --- | --- | --- |
| W5 · 19 Oct | E0, E4a | Extraction 2, Structured | No — existing cards |
| W6 · 26 Oct | **Harness**, E1, E2, E3b | Retrieval + one new seat | **Yes.** The capacity question is here and nowhere else |
| W7 · 2 Nov | E7, E4b, E6 | Retrieval, Structured | Rides "both tools real" |
| W8 · 9 Nov | **E13** | Structured + Extraction 1 | Eval round 1 week |
| W9 · 16 Nov | E8, E12 | Retrieval, Platform | Scale-up gate |
| W10 · 23 Nov | E15, then E9 and E10 if the gate opened | Retrieval | Rides the abstention card |

E1, E2 and the harness are the only genuinely new work. Everything else is a card that already exists, measured instead of merely done.

## If v1 is already good

If accuracy is close without ontology changes, record it as a finding and say so plainly: the ontology was not the bottleneck. That is a real result and it is cheaper to report than to bury.
