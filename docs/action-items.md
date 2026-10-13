# Action items

Rewritten 2026-10-13 after reading the Data and RAG repos. The earlier version had 58 items; most were asks for things another team had already built, or detail that belongs inside one of the five headings below.

## Where we actually stand

| Repo | Code |
|---|---|
| Data | 53 Python files, 5 migrations, tests, CI |
| RAG | 18 Python files, conformance tests, a mock, an eval harness |
| Us | the INT-0 mock in this repo, and nine documents |

Our contracts are in good shape. Our implementation is a week behind two teams who have been shipping.

---

## 1 · Build

**INT-0 was due 12 Oct** — mock `graph_search` and `structured_search` at the final signature, contract only, no real data.

- [x] Mock tools at the agreed shape, with 47 conformance tests — `mock/mock_retrieval_tools.py`, `graphrag/contract.py`
- [ ] Tell Agents it exists so they can build routing against it
- [ ] **Extraction prototype against public EDGAR filings.** Two or three 10-Ks, Pydantic plus structured outputs, mentions resolved to entities in a separate stage, loaded into Neo4j, one working `find_path`. Does not wait on Data
- [ ] Fact loader over the same filings, into DuckDB

Imperfect is fine. Absent is not — W8 Eval round 1 scores what exists.

## 2 · Data

Read [data-team-state.md](./data-team-state.md) first. Sections, table-cell provenance with XPath, inline-XBRL cell-to-fact links, dimensions, namespaces, raw-plus-parsed values and `reports.full_text` are all already built.

- [ ] **Read-only database credentials.** Everything we know is archaeology from migration files
- [ ] Withdraw the asks they have already satisfied, explicitly, before asking for anything else
- [ ] **Expose the natural key alongside the UUID**, so we anchor on `(report_id, section_key)` rather than a `gen_random_uuid()` that may not survive a rebuild. Ask whether `section_key` is content-derived or positional
- [ ] **`basis` on facts**, or confirmation we derive it from `amendment_of_id`. Confirmed absent; our most load-bearing gap
- [ ] **Note-level codes in `section_types`** — a lookup table, so an INSERT rather than a migration
- [ ] `sic`, `state_of_incorp`, `exchange`, `fiscal_year`, `fiscal_period`
- [ ] When do chunks land on `main`, and is `reports.sections` or `sec.report_sections` authoritative?
- [ ] Reverse our position on XPath in writing — they require it, Eval uses it, RAG reserves a key for it

## 3 · RAG

- [ ] Was `retrieval_api.md` v0 signed off on 8 Oct?
- [ ] Ask for **`section_id`** and **`quote`** — the only two things we cannot get from their schema
- [ ] Two they can check in minutes: `section_coverage` for the 10-Qs, and whether unassignable text returns `None` (contract §5) or `"Unlabeled"` (`chunk.py:82`)
- [ ] The three-way section conversation with Data and Eval — see [section-vocabulary.md](./section-vocabulary.md)

## 4 · Eval

- [ ] Send [eval-ask.md](./eval-ask.md) to Yulei Ding
- [ ] **The traversal tranche.** Neither gold set has a question requiring traversal. Without it, W8 cannot show the graph earned its place
- [ ] The abstention controls, M14–M16 — the only questions that catch a graph inventing edges
- [ ] Which pilot corpus is real: MSFT, the six banks and industrials, or `pilot-corpus-setup`?

## 5 · Agents

- [ ] Send [agent-notes.md](./agent-notes.md) and point at the mock
- [ ] **`structured_search` has no contract** — their §8 put XBRL out of v0 and nobody picked it up. Get a review slot
- [ ] `answers` is gone; citations must branch on which provenance key is populated

---

## Owners

Nothing above has a name against it. Seven people, two hats each, and the two teams moving fastest have four or five contributors in parallel branches. This is the item that makes the rest happen.

## Still unconfirmed

Fold these into the messages above rather than chasing separately: whether INT-0 happened, whether the W3 gate is 9 or 23 Oct, whether the 12 Oct sample filings arrived, and the 11 vs 17 Dec readout date.

## Deferred

The contested ontology list, `PEER_OF` sourcing, remaining Notion diagrams, the whitespace-normalisation detail, the fact-table confirmation (settled by evidence — just record it), and the Notion admin backlog. None changes what ships. Revisit once extraction runs.

Two Notion edits remain blocked by the workspace block limit; both are written verbatim in the 2026-10-13 vault handover.
