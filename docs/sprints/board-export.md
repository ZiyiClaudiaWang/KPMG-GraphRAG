# Board export — every task, with an owner

The 88 tasks from the Notion `GraphRAG Tasks` database, exported 15 Oct 2026. The
Columbia workspace is out of blocks and is no longer used; this file and the week
files beside it are the task list now.

Names come from the seat chart in [assignments.md](../assignments.md). Seats were
already on the board; names were not, on 74 of the 88.

**All 88 now have a name.** The co-lead split was settled on 15 Oct — Adeline holds
contract discussions, Kelly holds plan and finish — which named the last 41 at once.

Status came across as 81 not started and 7 in progress. W3 and W4 are both past, so
anything unticked there is overdue rather than planned. The three W3 plan-and-finish
cards were confirmed done on 15 Oct and are ticked here.

One card needs rewording before it is built: W4's *provenance stamping: source_chunk_id
on every node and edge*. Keeping `source_chunk_id` as an attribute is right; letting it
reach the edge key is what duplicated every edge on a re-chunk. See Layer 0 of
[schema-ids-operations.md](../schema-ids-operations.md).

## W3 · week of 2026-10-05

### Co-lead 1 — Adeline — contracts

- [ ] Evidence envelope field names agreed with RAG. Due 2026-10-07. Blocked by: RAG team. Both arms and theirs must return one shape.
- [ ] Ask Data for a sample chosen for entity-resolution stress. Due 2026-10-08. Blocked by: One company across 3+ years, one renamed or acquired, one 10-K/A alongside its original, overlapping Exhibit 21 lists. Chosen for stress rather than convenience, or we find the problems in W5 instead of W4.
- [ ] Schema v1 signed off with KPMG (W3 gate). Due 2026-10-09. Blocked by: Charter target is 14 Oct; the class master puts the W3 gate at Friday 9 Oct. Confirm which date holds. Sign-off gates v1, not the build start - proceed on v0 against public filings.
- [ ] Settle whether table-to-SQL is in scope. Due 2026-10-09. Blocked by: KPMG requested SQL as a possible third method on 2 Oct. Charter says review at W3 once schema v1 and the Data and RAG interfaces are settled. Separate two things: parameterised queries over a known XBRL fact schema, which we already own, from general text-to-SQL over arbitrary tables, which our scope fence excludes.
- [ ] Data / RAG interface spec agreed. *(in progress)* Due 2026-10-09. Blocked by: Emma
- [ ] Corpus versioning and chunk ID stability agreed with Data. Due 2026-10-09. Blocked by: Data team. Stable IDs across re-parses, or corpus_version on every node and edge.
- [ ] Structured / XBRL contract agreed with Data. Due 2026-10-09. Blocked by: Data team. Send the format request page first.
- [ ] Draft acceptance thresholds for Eval. Due 2026-10-09.
- [ ] Define contract with RAG team + naming conventions. *(in progress)* Due 2026-10-09.

### Co-lead 2 — Kelly — plan and finish

- [x] PM: confirm readout date and the freeze week move. *(done 15 Oct)* Due 2026-10-08. Blocked by: PM. 11 Dec vs 17 Dec is still assumed.
- [x] Confirm team availability for W5-W7, W10 and W11-W12. *(done 15 Oct)* Due 2026-10-09. Blocked by: Charter names these as protected weeks. W10 is Thanksgiving. Ask now, not in November.
- [x] Confirm double hat team working model and start assigning. *(done 15 Oct)* Due 2026-10-09.

### Extraction 2 — Alina

- [ ] Schema v0 draft circulated before the ontology session. *(in progress)* Due 2026-10-08. Blocked by: Charter lists two schema owners, due 10 Oct. Pull it earlier so it can be the strawman the working session reacts to.
- [ ] Temporal model and amendment decision record. *(in progress)* Due 2026-10-08. Blocked by: Needs a decision session with all seven. Hard gate before Graph v1.
- [ ] Ontology working session, all seven. Due 2026-10-09. Blocked by: Strawman circulated before the session. Rule in the room: an entity type only goes in if someone names a question it unlocks. End with a cut list, not just an add list. One person writes it up afterwards.
- [ ] Ontology and schema v1 signed off. *(in progress)* Due 2026-10-09.

### Structured — Ashley

- [ ] Fact table schema drafted from the XBRL contract. Due 2026-10-09.

### Retrieval — Alison

- [ ] Query patterns the schema has to support. Due 2026-10-09.

### Platform — Jacob

- [ ] Graph store chosen and dev environment running. *(in progress)* Due 2026-10-09. Blocked by: KPMG


## W4 · week of 2026-10-12

### Co-lead 1 — Adeline — contracts

- [ ] Acceptance thresholds agreed with Eval. Due 2026-10-15. Blocked by: Eval team. Needs a number, not an activity.
- [ ] Numbers decision confirmed against Business sample questions. Due 2026-10-15. Blocked by: Business persona and sample questions. Under a third numeric means switch to triples in the graph.
- [ ] INT-0 — hand both mocks to Agents. Due 2026-10-14. Blocked by: The numbers decision, which changes the signature. Settle it first.

### Co-lead 2 — Kelly — plan and finish

- [ ] Confirm Data sample arrived, or trigger the EDGAR fallback. Due 2026-10-12. Blocked by: Data team. Decide on the Monday, not the Wednesday.

### Extraction 1 — Claudia

- [ ] Toy graph loaded, roughly 100 nodes. *(in progress)* Due 2026-10-16.
- [ ] Extraction prototype running on 2-3 filings. Due 2026-10-14. Blocked by: Data parsed chunks.
- [ ] Provenance stamping: source_chunk_id on every node and edge. Due 2026-10-16.

### Extraction 2 — Alina

- [ ] Hand-label 50 subsidiary pairs as fuzzy ER ground truth. Due 2026-10-16. Blocked by: About an hour's work on the Exhibit 21 lists in the sample. Without it, the W5 spot-check on fuzzy matching is a judgement call rather than a measurement, and the W4 thresholds mean nothing for the fuzzy side.

### Structured — Ashley

- [ ] Mock structured tool at the final signature. Due 2026-10-14.
- [ ] Deterministic entity resolution on CIK, ticker and LEI. Due 2026-10-16. Blocked by: Moved to Structured 6 Oct: this is already the fact table's primary key, so splitting it means doing the same work twice.
- [ ] XBRL fact table loading with company and period dimensions. Due 2026-10-16. Blocked by: Data XBRL facts with dimensions intact.

### Retrieval — Alison

- [ ] Mock graph tool at the final signature. Due 2026-10-14.

### Platform — Jacob

- [ ] Graph store spike — run the four tests, pick one. Due 2026-10-12. Blocked by: Working default is Neo4j. The spike exists to disprove it, not to re-open the question from scratch. Four acceptance criteria on the page; any failure is disqualifying. Graph store and LLM budget both due by W4, before any scale-up.
- [ ] LLM token cost measured on the prototype. Due 2026-10-16.


## W5 · week of 2026-10-19

### Co-lead 1 — Adeline — contracts

- [ ] Coverage and precision spot-check against the thresholds. Due 2026-10-23.
- [ ] Multi-hop question set to Eval. Due 2026-10-23. Blocked by: Write from the filings, not the graph. Gold set v1 freezes in W6.

### Extraction 1 — Claudia

- [ ] Graph v1 loaded. Due 2026-10-23.
- [ ] Extraction v1 across 3-5 companies. Due 2026-10-23.

### Structured — Ashley

- [ ] Fact table v1 with dimensions. Due 2026-10-23.
- [ ] Structured query layer v0, parameterised over the fact schema. Due 2026-10-23.
- [ ] Numeric question set to Eval, exact-match. Due 2026-10-23.

### Retrieval — Alison

- [ ] Run Leiden on Graph v1 — do communities earn their place at W9?. Due 2026-10-23. Blocked by: Needs Graph v1. Half a day's work. The W9 community-summary task is currently scheduled on an untested assumption - that communities are meaningful at twelve companies. Edge et al.'s results come from much larger corpora. Better to find out in W5 than in the only fix-cycle week.

### Platform — Jacob

- [ ] Production corpus verified readable. Due 2026-10-19. Blocked by: Data production corpus.


## W6 · week of 2026-10-26

### Co-lead 1 — Adeline — contracts

- [ ] Graph / vector coupling decision with RAG and Agents. Due 2026-10-29. Blocked by: RAG and Agents. Must land before the retrieval v1 handoff.

### Co-lead 2 — Kelly — plan and finish

- [ ] INT-1 — structured arm live behind the signature. Due 2026-10-28.

### Extraction 2 — Alina

- [ ] Fuzzy entity resolution: subsidiaries, segments, officers. Due 2026-10-30.

### Structured — Ashley

- [ ] Structured tool v1 replaces its mock. Due 2026-10-27.

### Retrieval — Alison

- [ ] Graph-grounded retrieval prototype. Due 2026-10-30.

### Platform — Jacob

- [ ] Shared entity identity applied across graph and fact table. Due 2026-10-30.


## W7 · week of 2026-11-02

### Co-lead 1 — Adeline — contracts

- [ ] Structured arm scoring in Eval's harness. Due 2026-11-06. Blocked by: Eval team. Ask now rather than waiting for round 1.
- [ ] API integration with Agents. Due 2026-11-03.

### Co-lead 2 — Kelly — plan and finish

- [ ] INT-2 — both arms wired, rehearse the demo. Due 2026-11-04. Blocked by: Rehearse Wednesday, not Thursday morning.
- [ ] Thin end-to-end demo. Due 2026-11-05.

### Retrieval — Alison

- [ ] Graph retrieval v1. Due 2026-11-03.

### Platform — Jacob

- [ ] Demo environment running off something other than one laptop. Due 2026-11-03.


## W8 · week of 2026-11-09

### Co-lead 1 — Adeline — contracts

- [ ] Eval round 1 run against the gold set. Due 2026-11-09. Blocked by: Eval harness and the frozen gold set.
- [ ] Error taxonomy drafted. Due 2026-11-10.

### Co-lead 2 — Kelly — plan and finish

- [ ] Documentation started. Due 2026-11-13.
- [ ] Fix list ranked for W9. Due 2026-11-13. Blocked by: W9 is the only fix cycle. What does not make the list does not get fixed.

### Extraction 1 — Claudia

- [ ] Triage missing entities and wrong relations. Due 2026-11-11.

### Retrieval — Alison

- [ ] Triage retrieval misses. Due 2026-11-11. Blocked by: The answer was in the graph and we did not find it. Different fix from an extraction gap.


## W9 · week of 2026-11-16

### Co-lead 1 — Adeline — contracts

- [ ] Before and after metrics proving the fixes worked. Due 2026-11-20.

### Co-lead 2 — Kelly — plan and finish

- [ ] INT-3 — graph v2 and three-tool routing. Due 2026-11-19.

### Extraction 1 — Claudia

- [ ] Extraction fixes from the taxonomy. Due 2026-11-20.

### Extraction 2 — Alina

- [ ] Fuzzy entity resolution fixes. Due 2026-11-20. Blocked by: Measure against the W4 labelled pairs so the improvement is a number, not an impression.

### Retrieval — Alison

- [ ] Retrieval fixes. Due 2026-11-20.
- [ ] Community summaries for at-a-glance questions. Due 2026-11-20. Blocked by: Deferred here from W7 to protect the crunch. Cut it if the fixes run long.

### Platform — Jacob

- [ ] Token budget confirmed before any scale-up. Due 2026-11-18. Blocked by: Confirm first, scale second. Not the other way round.
- [ ] Corpus scale-up to 10+ companies. Due 2026-11-20.


## W10 · week of 2026-11-23

### Co-lead 1 — Adeline — contracts

- [ ] Routing recommendations agreed with RAG. Due 2026-11-25. Blocked by: RAG's own deadline is 25 Nov. Hard external date in a short week.
- [ ] Reproducibility checklist written. Due 2026-11-27.

### Co-lead 2 — Kelly — plan and finish

- [ ] Confirm who is actually available this week. Due 2026-11-23. Blocked by: Thanksgiving is Thursday. Ask Monday, not Wednesday.
- [ ] Documentation v1 draft complete. Due 2026-11-27. Blocked by: Split by component so it parallelises. There is no time for this after freeze.

### Structured — Ashley

- [ ] Amendments and restatements ingestion. Due 2026-11-27. Blocked by: Optional. Only if capacity allows. The modelling decision was W3 and is not optional.


## W11 · week of 2026-11-30

### Co-lead 1 — Adeline — contracts

- [ ] Frozen build verified against the evaluation results. Due 2026-12-02. Blocked by: Confirm what we are demoing is what we measured.
- [ ] Full rehearsal of the demo path. Due 2026-12-02.

### Co-lead 2 — Kelly — plan and finish

- [ ] Feature freeze. Due 2026-11-30. Blocked by: Anything unfinished is cut, not carried.
- [ ] Dry-run feedback triaged into W12. Due 2026-12-04. Blocked by: Decide what will not be fixed, out loud.
- [ ] Dry run. Due 2026-12-03.

### Platform — Jacob

- [ ] Production handoff. Due 2026-12-02.
- [ ] INT-4 — rebuild from a clean clone. Due 2026-12-01. Blocked by: Clean clone, not a working directory. Last chance to catch a laptop-only failure.


## W12 · week of 2026-12-07

### Co-lead 1 — Adeline — contracts

- [ ] Fresh-eyes test: someone who did not write the docs follows them. Due 2026-12-09. Blocked by: A handoff nobody has tried is a document, not a handoff.
- [ ] Deck support for Business and PM. Due 2026-12-10.

### Co-lead 2 — Kelly — plan and finish

- [ ] Handoff package assembled. Due 2026-12-09.
- [ ] Final readout. Due 2026-12-11. Blocked by: Date still assumed. Confirm with PM.
- [ ] Fix list locked. Due 2026-12-07.

### Extraction 1 — Claudia

- [ ] Dry-run fixes in extraction. Due 2026-12-08.

### Retrieval — Alison

- [ ] Dry-run fixes in retrieval. Due 2026-12-08.

### Platform — Jacob

- [ ] Final build tagged, repository tidy. Due 2026-12-09.
