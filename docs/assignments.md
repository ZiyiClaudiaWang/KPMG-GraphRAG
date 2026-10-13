# Who owns what

Created 2026-10-13. Maps the open items in [action-items.md](./action-items.md) onto the seven seats in the team model.

Seat labels stay fixed, so the plan survives a swap. Names go in the `Owner` lines and in the `Assignee` field on each task.

> **Fill the Owner lines before sharing this.** A seat chart with no names reads as a suggestion rather than an allocation.

---

## This week: the toy graph

Everything below is secondary to one deliverable — **a toy graph over two or three filings, built using the revised ontology.**

It is the W4 deliverable, not a contingency. Two things make it urgent beyond the date:

**It is the only test the ontology has.** We added a section hierarchy, `DOCUMENT`, `TABLE` and `CELL` this week on the strength of reading Data's schema. None of that has touched a real filing. If `SECTION CONTAINS SECTION` does not hold for a 10-K Item 8, or `CELL` turns out to be the wrong grain for an Exhibit 21, we want to know in W4 while the schema is still soft — not in W6 when extraction is built on it.

**It de-risks W5.** The team model warns W5 is the collision point: extraction v1, the fact table, the query layer, two question sets and the spot-check all land together with three people wearing both hats. That model assumes extraction *started* in W4. If it starts in W5 instead, the collision gets a week worse.

It does not wait on Data. Two or three 10-Ks pulled straight from EDGAR are enough, and the data contract already names this path.

---

## Co-leads

**Owners:**

Oversight, not build. Between them they own everything that is nobody else's job. The split below is by kind of work, not by seniority — swap it if the other division suits you better.

**Contract discussions — one co-lead holds all of these.** A second voice on a cross-team contract is how teams end up with two answers to the same question.

| # | Item | Counterparty |
|---|---|---|
| 1 | Read-only database credentials | Data |
| 2 | Stable identifier — `uuid5(namespace, accession_number + section_key)`, or expose the natural key. Is `section_key` content-derived or positional? | Data |
| 3 | `basis` on facts, or confirmation we derive it | Data |
| 4 | Note-level codes in `section_types` | Data |
| 5 | `sic`, `state_of_incorp`, `exchange`, `fiscal_year`, `fiscal_period` | Data |
| 6 | When do chunks land on `main`; is `reports.sections` or `sec.report_sections` authoritative | Data |
| 7 | Was `retrieval_api.md` v0 signed off on 8 Oct? | RAG |
| 8 | Ask for `section_id` and `quote` | RAG |
| 9 | `section_coverage` for the 10-Qs, and `None` vs `"Unlabeled"` | RAG |
| 10 | Schedule the three-way section conversation | RAG, Data, Eval |
| 11 | Send [eval-ask.md](./eval-ask.md) — traversal tranche, abstention controls, which pilot corpus is real | Eval |
| 12 | Send [agent-notes.md](./agent-notes.md), and tell them the mock exists | Agents |
| 13 | Get a review slot for the structured tool contract | Agents |

Items 1–6 are one message, 7–9 are one, 11 and 12–13 one each. Four messages, about a morning.

**Plan and finish — the other co-lead.**

- Resolve the four date contradictions: did INT-0 happen, is the W3 gate 9 or 23 Oct, did the 12 Oct samples arrive, is the readout 11 or 17 Dec
- Keep the decision log current. The fact-table decision is settled by evidence and still unrecorded
- Integration checkpoints INT-0 to INT-4, documentation, freeze, dry run, handoff, readout
- Second hat: back up Ontology and entity resolution, which is otherwise one person. Pair with Extraction 2 on the contested-node cut

---

## Extraction 1

**Owner:**

### Primary hat — graph construction

- [ ] **The toy graph.** Two or three 10-Ks from EDGAR, Pydantic plus OpenAI structured outputs, loaded into Neo4j, one working `find_path`
- [ ] Extract **mentions**, resolve to **entities** in a separate stage. Never let the model pick the canonical ID
- [ ] Report back what broke in the ontology. That feedback is half the point of the exercise

### Second hat — retrieval

- [ ] From W6, help query what you built. Pairs with the Retrieval seat so graph construction is never one person

---

## Extraction 2

**Owner:**

### Primary hat — ontology now, fuzzy entity resolution after

- [ ] **Validate the four new nodes against the toy graph's filings**, with Extraction 1. Does `SECTION CONTAINS SECTION` hold for a 10-K Item 8? Is `CELL` the right grain for an Exhibit 21? See [schema-ids-operations.md](./schema-ids-operations.md)
- [ ] **Close the contested list.** `EVENT`, `OFFICER`, `RISKFACTOR`, `FINANCIALMETRIC`, `PEER` — each needs a named question it unlocks, or it is cut
- [ ] Confirm `PEER_OF` is sourced from SIC, or drop it
- [ ] Then: subsidiary and segment-name matching

### Second hat — quality and evaluation

- [ ] Specify what graph path each of the 16 candidate questions in [eval-ask.md](./eval-ask.md) should exercise. The co-lead sends the ask; this seat defines what "the graph can answer this" means in practice
- [ ] From W8, judge extraction errors and help build the error taxonomy

---

## Structured

**Owner:**

### Primary hat — XBRL fact layer and deterministic entity resolution

- [ ] **DuckDB fact loader** over the same filings the toy graph uses
- [ ] Make the 10 quantitative gold questions pass
- [ ] Deterministic resolution on CIK and ticker — already the fact table's key
- [ ] **Contingency:** if Data says to derive `basis` rather than assert it, that lands here. It is a fact-table join on `amendment_of_id`

### Second hat — platform

- [ ] Share the loader and environment work, so stores are never one person. Peaks W7 and W11

---

## Retrieval

**Owner:**

### Primary hat — both retrieval tools behind the shared signature

- [ ] **Replace the INT-0 mock with the real tools without breaking a test.** `mock/mock_retrieval_tools.py` and the 47 conformance tests in `tests/test_contract.py` define the target exactly
- [ ] Keep `graphrag/contract.py` the single definition of the evidence shape. If the real tools need a field the mock lacks, change the contract first

### Second hat — fuzzy entity resolution

- [ ] Support Extraction 2 on subsidiary and segment-name matching. It is a similarity problem, which is the same shape as retrieval

---

## Platform

**Owner:**

### Primary hat — stores, loaders, builds, environments

- [ ] Neo4j and DuckDB running locally and reproducibly, so the toy graph has somewhere to land and nobody loses a day to setup
- [ ] **CI on this repo.** Data has `.github/workflows/`; we have 47 tests that nobody runs automatically
- [ ] Database connection once the credentials land
- [ ] Implement whichever identity scheme Data agrees to — `uuid5` or the natural key — in the loaders

### Second hat — structured

- [ ] Share the fact table with the Structured seat, so neither the graph nor the fact arm is one person deep

---

## While allocating

**Every stream is meant to have two people.** Graph construction has Extraction 1 and Retrieval. Structured has Structured and Platform. Ontology and entity resolution has Extraction 2 and a co-lead. If a name appears in only one place, the pairing has been lost and one illness stops a workstream.

**Second hats peak in different weeks from primaries.** Extraction 1 builds in W4–W5 and helps query in W6–W7. Extraction 2 defines identity early and judges errors at W8. Nobody should be doubly loaded in the same week — if the allocation produces that, the hats are paired wrongly.
