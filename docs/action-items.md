# Action items

Rewritten 2026-10-13 after reading the Data and RAG repos. The earlier version had 58 items; most were asks for things another team had already built, or detail that belongs inside one of the five headings below.

## Where we actually stand

| Repo | Code |
|---|---|
| Data | 53 Python files, 5 migrations, tests, CI |
| RAG | 18 Python files, conformance tests, a mock, an eval harness |
| Us | the INT-0 mock in this repo, and ten documents |

Our contracts are in good shape. Our implementation is a week behind two teams who have been shipping.

---

## 1 · Build

**INT-0 was due 12 Oct** — mock `graph_search` and `structured_search` at the final signature, contract only, no real data.

- [x] Mock tools at the agreed shape, with 63 conformance tests — `mock/mock_retrieval_tools.py`, `graphrag/contract.py`
- [ ] Tell Agents it exists so they can build routing against it
- [ ] **The toy graph — this week's deliverable.** Two or three 10-Ks from EDGAR, Pydantic plus structured outputs, mentions resolved to entities in a separate stage, loaded into Neo4j, one working `find_path`. Does not wait on Data, and it is the only test the revised ontology has
- [ ] Fact loader over the same filings, into DuckDB

Imperfect is fine. Absent is not — W8 Eval round 1 scores what exists. The toy graph is also what validates `Section CONTAINS Section`, `Document`, `Table` and `Cell` against a real filing; all four were added this week from reading Data's schema and none has been tested.

## 2 · Data

Read [data-team-state.md](./data-team-state.md) first. Sections, table-cell provenance with XPath, inline-XBRL cell-to-fact links, dimensions, namespaces, raw-plus-parsed values and `reports.full_text` are all already built.

- [ ] **Read-only database credentials.** Everything we know is archaeology from migration files
- [x] Withdraw the asks they have already satisfied — [data-contract.md](./data-contract.md) now opens with twelve of them
- [ ] **Expose the natural key alongside the UUID**, so we anchor on `(report_id, section_key)` rather than a `gen_random_uuid()` that may not survive a rebuild. Ask whether `section_key` is content-derived or positional
- [ ] **`basis` on facts**, or confirmation we derive it from `amendment_of_id`. Confirmed absent; our most load-bearing gap
- [ ] **Note-level sections.** A new `section_types` code is an INSERT, but the parser also has to detect note boundaries and emit a row per note — inline XBRL marks them, so it is structural, but it is not free
- [ ] `sic`, `state_of_incorp`, `exchange`, `fiscal_year`, `fiscal_period`
- [ ] When do chunks land on `main`, and is `reports.sections` or `sec.report_sections` authoritative?
- [x] Reverse our position on XPath in writing — done in the contract, `architecture.md` §2 and the schema doc

## 3 · RAG

- [ ] Was `retrieval_api.md` v0 signed off on 8 Oct?
- [ ] Ask for **`section_id`** and **`quote`** — the only two things we cannot get from their schema
- [ ] Two they can check in minutes: `section_coverage` for the 10-Qs, and whether unassignable text returns `None` (contract §5) or `"Unlabeled"` (`chunk.py:82`)
- [ ] The three-way section conversation with Data and Eval — see [section-vocabulary.md](./section-vocabulary.md)

## 4 · Eval

Eval approved RAG's v0 on 14 Oct with six requests. Four touch us — see [eval-reply.md](./eval-reply.md).

- [ ] Send [eval-ask.md](./eval-ask.md) to Yulei Ding. First confirm whether the earlier draft ever reached Eval — Gold Set v1 credits only RAG's v0 and Eval's own additions
- [ ] **The traversal tranche.** Gold Set v1 has no question requiring traversal, in 100. Without one, W8 cannot show the graph earned its place
- [ ] **M14.** Gold Set v1 has ten abstention questions and all ten test a missing fact. None tests a fabricated relationship, which is the failure mode specific to our arm
- [ ] Which pilot corpus is real — now three, not two: the six banks and industrials, our MSFT/JPM/AAPL/ADM pilot, or Eval's MSFT-plus-peers. They overlap by ticker, not by filing
- [ ] Ask Data for Microsoft's FY2024 10-K. One accession, and it unblocks M05 while strengthening M04 and M06
- [ ] Nominate a renamed or acquired registrant for M07 and M08, or drop both
- [ ] **Citation scoring must join on the provenance key, not `chunk_id`.** Ours is always null, so graph and structured evidence would score as uncited rather than scoring badly
- [ ] Confirm whether rerun scoring keys on `index_version` alone — ours moves `extraction_version`
- [ ] Get it in writing that scores are not comparable across tools, before an abstention threshold is fitted on one arm and applied to all three
- [x] `trace_id` on both tools, echoed at `metadata.trace_id`

**Two things the ask commits us to**, decided 22 Oct when it was sent:

- **Fri 23 Oct is external.** We promised Eval the graph path and the filings each of the nineteen questions joins, whatever they decide on the tranche. Eleven already name their filings in [eval-ask.md](./eval-ask.md), so eight remain. It is E0 on Extraction 2's W5 card and we chose to hold the card rather than slip it. If it slips, say so before the day rather than on it.
- **The `hops` labelling is ours if Eval says no.** We offered to take it. That offer was costed against 42 questions and Gold Set v1 makes it 100, so it is a card on the quality and evaluation hat, not a favour squeezed in around other work. We kept the offer because the field is the x-axis for both the retriever comparison and the `max_hops` default, and an ask that acknowledges no cost is how an ask gets quietly dropped.

## 5 · Agents

- [ ] Send [agent-notes.md](./agent-notes.md) and point at the mock
- [ ] **`structured_search` has no contract** — their §8 put XBRL out of v0 and nobody picked it up. Get a review slot
- [ ] `answers` is gone; citations must branch on which provenance key is populated

---

## Owners

All seven seats are claimed and named in [assignments.md](./assignments.md). Settled 15 Oct: **Adeline holds contract discussions, Kelly holds plan and finish.** Every contract item above also names a second seat — the one that interfaces most with that counterparty, who drafts the content and reads the reply. Adeline is still the only one who sends.

## Confirmed 15 Oct

The four open dates, closed:

- **INT-0 happened**, a day late. The mocks are at the final signature with 63 conformance tests. Agents have still not been told they exist, which is the outstanding half.
- **The W3 gate is 9 Oct**, and it has not closed. The board resolves the charter-versus-class conflict: sign-off gates v1, not the build start, so work proceeds on v0 against public filings.
- **The 12 Oct sample filings did not arrive.** The EDGAR fallback is now the path, not the contingency. It was always the plan for the toy graph, so nothing is blocked — but the read-only credentials ask gets more urgent, not less, because archaeology from migration files is now the only thing we know about their schema.
- **The readout is Fri 11 Dec.** Freeze Mon 30 Nov, INT-4 Tue 1 Dec, dry run Thu 3 Dec.

## Deferred

The contested ontology list, `PEER_OF` sourcing, remaining Notion diagrams, the whitespace-normalisation detail, the fact-table confirmation (settled by evidence — just record it), and the Notion admin backlog. None changes what ships. Revisit once extraction runs.

Two Notion edits remain blocked by the workspace block limit; both are written verbatim in the 2026-10-13 vault handover.
