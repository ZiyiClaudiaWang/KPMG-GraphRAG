# Action items — consolidated

As of 2026-10-13. Everything open across the Data contract, the RAG interface, Eval, Agents and our own docs.

Ordered by what blocks what, not by effort.

---

## 0 · Confirm before anything else

Three facts we are currently guessing at. All are cheap to check and expensive to be wrong about, and two of them change what the rest of this list means.

| # | Question | Why it matters |
|---|---|---|
| 0.1 | **Was the RAG *contract* v0 signed off?** `kpmg-rag-retrieval/docs/retrieval_api.md`, "Final v0, proposed for team sign-off on 2026-10-08". Not the gold sets — both of those are self-declared drafts | If signed, our conforming is settled and the ask narrows to two additive fields. If not, `section_id` is far cheaper to get in now than later |
| 0.2 | **Did INT-0 happen, and is the envelope frozen?** Notion puts it at W4 · 12 Oct | We have been editing the evidence shape after its own freeze date. Either INT-0 slipped, or the change needs an explicit amendment note before Agents builds on the old shape |
| 0.3 | **When does the W3 gate close — 9 Oct or 23 Oct?** The Notion home page says 23 Oct; the data contract, all handovers and the class master say 9 Oct | The same page puts INT-0 at 12 Oct, which would close W3 eleven days after W4 starts. One of them is wrong |

| 0.4 | **Did the sample filings arrive?** The contract asked for 2–3 filings in final format by **Mon 12 Oct**, which has passed | If they slipped, the EDGAR fallback below fires today rather than whenever someone notices |
| 0.5 | **Which pilot corpus is real?** MSFT, the six banks and industrials in `gold_v0.jsonl`, or the `pilot-corpus-setup` branch | Our stress sample to Data has to match it, and six companies may already satisfy the overlapping-Ex-21 requirement |

Also unresolved and lower stakes: final readout 11 Dec vs 17 Dec (confirm with PM).

---

## 0b · The one thing that does not wait for anyone

- [ ] **Start the prototype extraction pipeline against public EDGAR filings now.** The data contract already names this as the fallback if the sample slips, and 12 Oct has passed. Pull two or three 10-Ks straight from EDGAR, run extraction end to end, and find the problems that only real records surface — missing dimensions, negative-number formatting, offsets that do not line up.

  This is a stopgap for our own testing, not a substitute for the corpus, and the code must not assume anything the contract has not agreed. But every day spent waiting on Data is a day we cannot get back, and W4 is on the critical path for both arms.

---

## 1 · Data team

⚠️ **Read [data-team-state.md](./data-team-state.md) before sending anything.** Most of our contract is already built on `michaelliruoxi/data_engineering_kpmg@main`, including sections as first-class records, table-cell provenance with XPath, inline-XBRL cell-to-fact links, dimensions, namespaces, raw-plus-parsed values and the normalized full text. Sending a team a list of things they finished last week costs credibility we need for the asks that remain.

**Withdraw before re-sending**
- [ ] Cut the sections-as-first-class ask — `sec.report_sections` exists
- [ ] Cut the table-cell provenance ask — `sec.table_cells` exists, with a required XPath
- [ ] Cut the inline-XBRL offsets ask — `sec.cell_fact_links` does this
- [ ] Cut the keep-dimensions, keep-namespace, raw-plus-parsed and precision asks — all present
- [ ] Cut the "is the normalized document retrievable" question — `reports.full_text`
- [ ] Note that their naming already matches RAG's, so our rename is confirmed from two directions

**The asks that survive, smallest and most concrete first**
- [ ] **Expose the natural key alongside the UUID**, so we anchor on `(report_id, section_key)` rather than a `gen_random_uuid()` that may not survive a rebuild into a fresh database. Costs them nothing
- [ ] Ask whether `section_key` is derived from content or from position in the parse
- [ ] **`basis` on facts** (`as_filed` / `as_restated`), or confirmation we derive it from `amendment_of_id`. Confirmed absent, and still our most load-bearing gap
- [ ] **Note-level codes in `section_types`** — it is a lookup table, so this is an INSERT rather than a schema change. See [section-vocabulary.md](./section-vocabulary.md)
- [ ] `sic`, `state_of_incorp`, `exchange` on `companies`
- [ ] `fiscal_year`, `fiscal_period` on `filings` — RAG filters on both
- [ ] Ask when chunks land on `main`. `chunking.py` and `section_id_for_chunk` exist but no chunk table is merged; likely on `ruby/chunking-citations`
- [ ] Ask which is authoritative — `reports.sections` jsonb or the `sec.report_sections` table — and what stops them drifting
- [ ] **Reverse our position on XPath in writing.** They require it, Eval uses it, RAG reserves a key for it. We were the outlier insisting on normalized offsets
- [ ] File exports **in addition to** the Postgres, framed as database-as-record / files-as-interface
- [ ] **Read-only database credentials**, so this stops being archaeology from migration files
- [ ] Tell them `gold_set_v0.md` must never be indexed

**Still unanswered**
- [ ] Which tickers, which years, which form types
- [ ] Are DEF 14A or Form 4 in scope, or do officers come out of the graph
- [ ] Are exhibits parsed in practice? `source_documents.role` and `document_type` allow it — is Ex-21 actually among them?

---

## 2 · RAG team

We conform to their vocabulary. See [interface-rag-gap.md](./interface-rag-gap.md).

**Ask for**
- [ ] **`section_id`** — their `chunk_id` is stable only while `chunking_version` is unchanged, which is exactly the guarantee the graph cannot build on
- [ ] **`quote`** — the matched span inside `text`, for highlighting. Lower priority
- [ ] Decide whether `section_path` sits top-level or in `metadata` — their call, and `metadata` needs no version bump

**Two things they can check in minutes**
- [ ] What is `section_coverage` for the 10-Qs in the pilot corpus? Their `chunk.py` already computes it
- [ ] Contract §5 says unassignable text returns `None`; `chunk.py:82` emits `"Unlabeled"`. Which is it? A consumer branching on `None` silently misses those chunks

**The eight reconciliation items beyond naming** — full list in [interface-rag-gap.md](./interface-rag-gap.md). The two that block work:
- [ ] **`structured_search` has no contract at all.** Their §8 put XBRL and text-to-SQL out of scope for v0 and nobody picked it up
- [ ] **Section vocabulary** — the three-way conversation below

---

## 3 · The three-way section conversation

Data, RAG and Eval in one room. Blocks both our extraction unit and RAG's filter design.

- [ ] Schedule it
- [ ] Show the three-source table and the `ITEM_RE` regex — their own files make the case better than our argument does
- [ ] Walk the six decisions in [section-vocabulary.md](./section-vocabulary.md). Five have a recommendation attached; only the `section_path` placement is genuinely open

---

## 4 · Eval team

Send [eval-ask.md](./eval-ask.md) to Yulei Ding.

- [ ] **Ask for a traversal tranche** — 15–20 questions inside their existing format. Two gold sets exist (30 MSFT questions in `gold_set_v0.md`, 12 across six companies in `eval/gold_v0.jsonl`). The JSONL has four cross-company questions, but all four are fan-out — three parallel lookups compared afterwards. Neither set has a question requiring traversal
- [ ] **Ask which pilot corpus is real** — MSFT, the six banks and industrials, or the `pilot-corpus-setup` branch. Our stress sample to Data should match it
- [ ] Corpus requirement: 10-Ks not only 10-Qs, three companies, overlapping Ex-21 lists, one company across 3+ years, one 10-K/A. Same stress sample already outstanding with Data
- [ ] Propose shapes, let Eval own the wording. We should not author the benchmark our own arm is graded on
- [ ] Ask for a `multihop` tier label so graph questions report as their own slice
- [ ] Confirm scoring stays on `contains` matching, with XPath kept as a human audit trail
- [ ] Agree whitespace normalisation on both sides of a `contains` match
- [ ] **Abstention controls** — M14–M16. A graph that invents edges scores well on everything else
- [ ] **Provenance-correctness sample** at W4 — does the cited evidence actually support the claim? `evidence_recall` cannot see this
- [ ] Acceptance thresholds for extraction precision and numeric tolerance, owed at W4

---

## 5 · Agents team

Send [agent-notes.md](./agent-notes.md).

- [ ] Tell them `answers` is gone — retrieval returns evidence, they compose
- [ ] One error rule across three tools: `[]` means no evidence, an exception means broken
- [ ] Never threshold scores, never compare them across tools
- [ ] Citations must branch on which of `chunk_id` / `section_id` / `metadata.fact_id` is populated
- [ ] **Get a review slot for the structured tool contract**
- [ ] Agree how a composed hybrid answer attributes three citation styles, before W7 rather than after
- [ ] Via Agents, ask UI to add `section_id` and `metadata.fact_id` to what citations read. Their current five fields cite at document level only

---

## 6 · Our own docs and schema

- [ ] Get [PR #1](https://github.com/ZiyiClaudiaWang/KPMG-GraphRAG/pull/1) reviewed and merged
- [ ] Fix the section key — `hash(accession + item + char_start)` is offset-dependent, so `section_id` survives re-chunking but not re-normalising. Key it off `accession_number + section_path + ordinal`
- [ ] Add the three-source table and the `chunk.py` evidence into `section-vocabulary.md`
- [ ] Close the fact-table decision and cite the gold set — 10 quantitative plus 10 comparative of 30 is over half, well above the one-third switch threshold
- [ ] Decide whether to port the two all-workstreams diagrams from Notion
- [ ] Walk the contested ontology list — EVENT, OFFICER, RISKFACTOR, FINANCIALMETRIC, PEER. Each needs a question it unlocks or it is cut
- [ ] Confirm `PEER_OF` is sourced from SIC, or drop it
- [ ] **Assign an owner to each item in this file.** Seven people, two hats each, and nothing here currently has a name against it — which is how cross-team asks quietly become nobody's job

---

## 7 · Notion and admin

- [ ] **Two table-cell edits are still blocked** on `Schema, IDs, operations and contracts` — the Layer 0 provenance row and the Layer 1 `SECTION` row. Both are written verbatim in the 2026-10-13 vault handover. Notion rebuilds table rows on edit, so a cell change counts as block creation
- [ ] Free blocks or upgrade the Columbia workspace
- [ ] Delete the superseded copy in the Dara HQ workspace — search still surfaces it above the live pages
- [ ] Tell PM the check-in workbook has RAG and GraphRAG sharing one Update cell
- [ ] Sign off the scope fence with RAG, Data, Agents, Eval, PM
- [ ] Confirm structured / SQL ownership with KPMG
