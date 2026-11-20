# Week of 16 Nov 2026 — acceptance criteria

Companion to [2026-11-16.md](./2026-11-16.md). That file says who is doing what; this
one says how each of us knows it is finished, in enough detail that someone who did
not do the work can check it.

Three rules, taken from the sprint README and applied here:

- **Someone else can check it.** "Routing improved" is not a criterion. "The share of
  questions reaching the right arm, before and after, on the same 155 questions" is.
- **A number is a number, not an impression.** Where a criterion asks for a figure, an
  estimate does not close it.
- **A criterion can close as "no, and here is why".** A written negative result counts;
  silence does not. Several of these are genuinely at risk this week and that is fine
  as long as it is said.

---

## Co-lead, contracts — Adeline

### Routing rules agreed with Agents

- [ ] Layer 3 of `schema-ids-operations.md` has a row per question type, each naming
      which arm serves it
- [ ] Every row that round 1 contradicted is changed, and the change cites the
      questions that forced it
- [ ] Rows round 1 could not test are marked untested rather than left looking
      confirmed
- [ ] Agents have replied in the thread saying they will route to it, or named what
      they will not
- **Closes when:** the table matches evidence and Agents have said so in writing.

### US-GAAP labels

- [ ] `"top line"`, `"bottom line"`, `"sales"` and the rest of the agreed synonym list
      resolve to US-GAAP concepts
- [ ] The street-address case is a regression test, not a fixed anecdote
- [ ] A synonym with no safe mapping returns nothing rather than a near miss
- [ ] The list is a reviewable file, not a dictionary buried in a function
- **Closes when:** the demo question that returned a street address returns the
      revenue figure, and a test fails if it regresses.

### Text2Cypher — step 1

- [ ] `graphrag/cypher_backend.py` exports `search(query, filters, top_k, trace_id)`
- [ ] A stubbed model returning hand-written Cypher produces contract-conforming
      evidence end to end, with no LLM call anywhere in the path
- [ ] Every row of the failure table in [text2cypher-scope.md](../text2cypher-scope.md)
      has a test: parse failure, guard rejection, valid-but-empty, timeout, database
      down, model down
- [ ] The test module sorts after `tests/test_contract.py` and CI is green
- [ ] It merges on its own, without the prompt work
- **Closes when:** PR #67's seam registers a real `cypher_search` and 195-plus tests
      still pass.

### Contract changes round 1 forced

- [ ] Each change is in the document it belongs to, not only in the code that fixed it
- [ ] Each says what round 1 did that prompted it
- **Closes when:** a reader of the contracts alone would not be surprised by the code.

---

## Co-lead, plan and finish — Kelly

### Scope freeze proposal

- [ ] Two lists: what ships by 30 Nov, and what does not
- [ ] Every cut item says who it affects and what breaks without it
- [ ] The Text2Cypher arm and hybrid retrieval each appear on one of the two lists by
      name, since both are new this month
- [ ] Circulated with enough time that disagreement lands before the freeze, not on it
- **Closes when:** nobody is still assuming an item ships that is on the cut list.

### Documentation plan for handoff

- [ ] Names every document that must exist by the readout, and who writes it
- [ ] Says which already exist and are stale — `docs/sprints/2026-11-09.md` and the
      Subsidiary claim in `docs/dashboard-plan.md` are both known
- [ ] Includes the one test: someone outside the team clones the repo and runs both
      arms from what is written
- **Closes when:** the list exists with names and dates against it.

### Round 1, read before anyone reacts

Carried from last week and still open. It is the input to the freeze proposal, so it
comes first.

- [ ] One page, separating "the graph is wrong" from "the question did not need a
      graph"
- [ ] The 57 red and 24 yellow questions are each on one side of that line
- [ ] Three things round 2 should change. Three, not thirty
- **Closes when:** the page is circulated and the three are agreed.

---

## Extraction 1 — Claudia

### Graph v2 over a wider corpus

- [ ] The corpus width is the number the co-leads settled, not a number chosen during
      the run
- [ ] Loading twice produces the same counts — run it twice and show both
- [ ] The re-chunk creates no duplicate edge: `SUPPORTS` count equals `Claim` count,
      as it does today at 10,774
- [ ] Every node carries `pipeline_scope`, and the new scope is distinguishable from
      `kelly-extraction-pilot-v1`
- [ ] `tools/corpus_report.py` runs clean against it, with no orphans
- **Closes when:** two consecutive loads agree, and the report shows the larger corpus
      with the same structural invariants.

### Toy-graph checks against v2

- [ ] `tests/test_toy_graph_fixture.py` passes against the v2 schema
- [ ] Both findings behave as the schema says, or the schema changes and the change is
      written down
- **Closes when:** the check is green or the schema document has a new decision row.

### E8, hop depth

- [ ] A results row per run in the versioned CSV, carrying `extraction_version`,
      `index_version`, `corpus_version` and the four metrics
- [ ] `max_hops` has a default and the number that chose it
- **Closes when:** the default is in the code and the row is in the CSV.

---

## Extraction 2 — Alina

### Ontology changes round 1 forced

- [ ] Each is a decision row naming the question it answers
- [ ] A change with no question behind it is dropped, and the drop is recorded
- **Closes when:** every row names a question, and no question names a missing row.

### Error taxonomy v2, with counts

- [ ] Every failed round 1 question has exactly one cause: bad extraction, missing
      edge, wrong grain, or the question did not suit a graph
- [ ] Each cause carries a count across the 57 red and 24 yellow
- [ ] It says which error type is worth fixing next, and what fixing it would be
      expected to recover
- **Closes when:** Claudia and Alison can each point at their next fix without asking
      which one matters.

### E14, subsidiary to segment

Blocked until the Exhibit 21 load runs on the instance being measured. `internal` has
752 subsidiaries today; `canonical` has none.

- [ ] A result row, or a written line saying it could not run and what it waited on
- **Closes when:** either exists. Not when the week ends quietly.

---

## Structured — Ashley

### Scale the fact table to the wider corpus

- [ ] Same corpus width as the graph, so the two arms stay comparable
- [ ] Load time and query time both written down, with the hardware they were measured
      on
- [ ] Per-filing counts checked for a thin filing — CASY at 327 facts is the current
      floor and the shape to watch for
- [ ] `store_meta_v1` carries a `corpus_version` that distinguishes this store from
      `data-api-raw:20`
- **Closes when:** both times are numbers in the repo and no filing loaded thin
      unnoticed.

### Segment breakdowns flag the consolidated total

- [ ] A query returning segment slices marks the consolidated figure as consolidated
- [ ] A total is never silently summed with its own parts
- [ ] The two known ranking bugs — slices outscoring the consolidated figure, a slice
      standing in for a company total — each have a test
- **Closes when:** the Ask tab shows the consolidated row distinguishable from its
      slices, and both tests fail if that stops being true.

---

## Retrieval — Alison

### Abstention, per arm

- [ ] Each arm has its own floor and its own tie rule
- [ ] Each number records what it was fitted on and what it trades away
- [ ] A number fitted on one arm is never applied to another, and the document says so
      in the words Eval agreed to
- [ ] The graph arm's floor says when it expires. Its score is Neo4j's full-text
      relevance, which moves with the corpus, so a number fitted at twenty filings
      does not survive the v2 load
- [ ] Scale-free rules are preferred where one will do. The gap between the top two
      results needs no normalisation and works on both arms; an absolute floor does
      not
- **Closes when:** four numbers and four reasons exist, and each says what would
      invalidate it.

### Future-period questions abstain on coverage

- [ ] A per-company bound exists: the latest `reporting_period_end` we hold for that
      ticker, not a single date for the corpus
- [ ] A question asking beyond that bound abstains without consulting a score
- [ ] All five round 1 cases abstain — EV1-063, EV1-065, EV2-046, EV2-047, EV2-050
- [ ] A 53-week fiscal year and a non-calendar year-end are each covered by a test.
      Both are in the round 1 set already
- [ ] The reason emitted is whichever the co-lead settles with Eval. It cannot be
      invented here — the validator rejects anything outside `SEARCH_REASONS`
- **Closes when:** the five abstain, and none of them needed a threshold to do it.

### Period-aware ranking

Comes first. The coverage check above cannot tell a wrong-period fact from a right one
until this lands.

- [ ] `score_fact()` reads `period_start` and `period_end`
- [ ] Two facts differing only in period no longer score the same
- [ ] The nine-month-versus-three-month case has a test against the real store
- **Closes when:** asking for a three-month figure stops returning the nine-month one
      at the same score.

### Text2Cypher interface questions

Not a build item this week. The guard has nothing to sit on until step 1 lands.

- [ ] Answered: does the guard refuse by raising, or by returning a reason the adapter
      turns into `[]`?
- [ ] Answered: what one line does the log write per call, field by field?
- [ ] Both answers are in [text2cypher-scope.md](../text2cypher-scope.md), not only in
      a thread
- **Closes when:** Adeline can build step 2 without asking either question again.

### E7, retriever comparison

- [ ] Both arms run on the same questions, same filters, same `top_k`
- [ ] A results row in the versioned CSV
- **Closes when:** the comparison is a row, not a recollection.

---

## Platform — Jacob

### Scale check at the wider corpus

- [ ] The answer is a number: what fell over, at what size, or nothing did at the size
      tested
- [ ] Load, query and the Aura cold start are each timed
- [ ] If nothing broke, the criterion is the size that was actually reached, not "it
      seemed fine"
- **Closes when:** a number exists for the size tested.

### CI green at the new size

- [ ] The full suite runs in CI, not locally
- [ ] Bare `pytest` still collects — a new test module sorting before
      `tests/test_contract.py` takes the whole run down
- [ ] Any test skipped at the new size says why in the skip reason
- **Closes when:** a green run exists on a PR at the new corpus size.

### Hybrid retrieval

New this week and scoped nowhere in the repo, so the deliverable is the scope.

- [ ] Names which two arms combine, and where the merge happens — in the arm, in
      `retrieve.search`, or in the Agent
- [ ] Names one question it answers that neither arm answers alone
- [ ] Says what it does to the one-provenance-key rule, since a merged result has two
      sources
- [ ] Says whether it is a fourth tool name or a mode, and why — #67 took the
      third-tool route for a reason that applies here
- [ ] Short enough that Kelly can put it on a freeze list this week
- **Closes when:** the scope is in `docs/` and the freeze decision can be made on it.

### E12, cost per filing

- [ ] A figure per filing, covering extraction and load
- [ ] The corpus-width decision cites it
- **Closes when:** the number exists before the width is chosen, not after.

---

## What closes the week

INT-3, and nothing else, is the week's goal: graph v2 built on what round 1 taught,
and the Agent routing to the right tool more often than not. Each seat above has one
item that serves it directly — Adeline's routing table, Claudia's v2 load, Alina's
taxonomy, Ashley's scaled store, Alison's routing measurement, Jacob's scale check.
The rest is real work that can slip a week without the goal slipping with it.
