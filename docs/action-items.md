# Action items — consolidated

As of 2026-10-13. Everything open across the Data contract, the RAG interface, Eval, Agents and our own docs.

Ordered by what blocks what, not by effort.

---

## 0 · Confirm before anything else

Three facts we are currently guessing at. All are cheap to check and expensive to be wrong about, and two of them change what the rest of this list means.

| # | Question | Why it matters |
|---|---|---|
| 0.1 | **Was RAG's `retrieval_api.md` v0 signed off?** It says "proposed for team sign-off on 2026-10-08" | If signed, our conforming is settled and the ask narrows to two additive fields. If not, `section_id` is far cheaper to get in now than later |
| 0.2 | **Did INT-0 happen, and is the envelope frozen?** Notion puts it at W4 · 12 Oct | We have been editing the evidence shape after its own freeze date. Either INT-0 slipped, or the change needs an explicit amendment note before Agents builds on the old shape |
| 0.3 | **When does the W3 gate close — 9 Oct or 23 Oct?** The Notion home page says 23 Oct; the data contract, all handovers and the class master say 9 Oct | The same page puts INT-0 at 12 Oct, which would close W3 eleven days after W4 starts. One of them is wrong |

Also unresolved and lower stakes: final readout 11 Dec vs 17 Dec (confirm with PM).

---

## 1 · Data team

The rewritten contract is in [data-contract.md](./data-contract.md). It differs materially from what they last read, so it needs re-sending rather than a nudge.

**Send**
- [ ] Re-send the data contract, pointing at the change table at the bottom
- [ ] Tell them `gold_set_v0.md` must never be indexed — their repo already holds the same MSFT filing

**The asks, largest first**
- [ ] **Sections as first-class records**, with chunks carrying a parent `section_id`. Our single biggest ask
- [ ] **Sections below Item level**, so a note is its own section — see [section-vocabulary.md](./section-vocabulary.md)
- [ ] `basis` on facts (`as_filed` / `as_restated`), or confirmation that we derive it from `amends_accession_number`
- [ ] A provenance shape for table cells — `table_id` + row + col — and `section_id` on table records. Ex-21 edges have no citable key today
- [ ] Split `corpus_version` into `extraction_version` and `chunking_version`. Re-normalising invalidates every offset in the corpus and nothing currently catches it
- [ ] Confirm `section_id` and `fact_id` are theirs to define, so we delete our competing definitions
- [ ] File exports **in addition to** the Postgres, framed as database-as-record / files-as-interface
- [ ] Read-only database credentials for Adeline

**Still unanswered from the original contract**
- [ ] Which tickers, which years, which form types
- [ ] Are exhibits parsed at all, and is Ex-21 among them
- [ ] Are DEF 14A or Form 4 in scope, or do officers come out of the graph
- [ ] Which XBRL library, and does it keep dimensions
- [ ] Is the normalized document retrievable, or only chunks
- [ ] Where the corpus lives and how we read it

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

- [ ] **Ask for a multi-hop tranche** — 15–20 questions inside their existing format. Nothing in the current 30 measures the graph arm
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

---

## 7 · Notion and admin

- [ ] **Two table-cell edits are still blocked** on `Schema, IDs, operations and contracts` — the Layer 0 provenance row and the Layer 1 `SECTION` row. Both are written verbatim in the 2026-10-13 vault handover. Notion rebuilds table rows on edit, so a cell change counts as block creation
- [ ] Free blocks or upgrade the Columbia workspace
- [ ] Delete the superseded copy in the Dara HQ workspace — search still surfaces it above the live pages
- [ ] Tell PM the check-in workbook has RAG and GraphRAG sharing one Update cell
- [ ] Sign off the scope fence with RAG, Data, Agents, Eval, PM
- [ ] Confirm structured / SQL ownership with KPMG
