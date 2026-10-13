# Who owns what

Created 2026-10-13. Maps the twenty open items in [action-items.md](./action-items.md) onto the seven seats in the team model.

Seats are from the team model; names go in the `Owner` column and in the `Assignee` field on each task. Seat labels stay fixed, so the plan survives a swap.

> **Fill the Owner column before sharing this.** A seat chart with no names reads as a suggestion rather than an allocation.

---

## Co-lead 1 — everything crossing a team boundary

**Owner:** Adeline

Thirteen of the twenty items. Nobody else should touch these; a second voice on a contract discussion is how teams end up with two answers to the same question.

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

Items 1–6 are one message. 7–9 are one message. 11 and 12–13 are one each. Four messages, about a morning.

## Co-lead 2 — the plan and the finish

**Owner:**

| Item | Why this seat |
|---|---|
| Resolve the four date contradictions — did INT-0 happen, W3 gate 9 or 23 Oct, did the 12 Oct samples arrive, readout 11 or 17 Dec | Owns scope against the master timeline and INT-0 to INT-4 |
| Keep the decision log current — the fact-table decision is settled by evidence and still unrecorded | Owns the decision log |
| Pair with Extraction 2 on the contested-node cut | Backs up Ontology, which is otherwise one person |

## Extraction 1 — graph construction

**Owner:**

**This seat is the critical path and is currently behind.** Peak is W4–W5, it is W4, and there is no extraction code.

- [ ] **Extraction prototype against public EDGAR filings.** Two or three 10-Ks, Pydantic plus OpenAI structured outputs, loaded into Neo4j, one working `find_path`
- [ ] Extract **mentions**, resolve to **entities** in a separate stage. Never let the model pick the canonical ID
- [ ] Does not wait on Data. The data contract already names this as the fallback and the 12 Oct sample date has passed

Everything else on the list can slip a week without changing the outcome. This cannot.

## Extraction 2 — ontology, then fuzzy entity resolution

**Owner:**

- [ ] **Close the contested list.** `EVENT`, `OFFICER`, `RISKFACTOR`, `FINANCIALMETRIC`, `PEER` — each needs a named question it unlocks, or it is cut
- [ ] **Validate the four new nodes against real filings.** Does `SECTION CONTAINS SECTION` hold for a 10-K Item 8? Do `TABLE` and `CELL` survive contact with a real Exhibit 21? See [schema-ids-operations.md](./schema-ids-operations.md)
- [ ] Confirm `PEER_OF` is sourced from SIC, or drop it
- [ ] Then: subsidiary and segment-name matching

**Second hat, quality and evaluation:** specify what graph path each of the 16 candidate questions in [eval-ask.md](./eval-ask.md) should exercise. Co-lead 1 sends the ask; this seat defines what "the graph can answer this" means in practice.

## Structured — XBRL fact layer and deterministic entity resolution

**Owner:**

- [ ] **DuckDB fact loader** over the same EDGAR filings Extraction 1 is using
- [ ] Make the 10 quantitative gold questions pass
- [ ] Deterministic resolution on CIK and ticker — already the fact table's key
- [ ] **Contingency:** if Data says to derive `basis` rather than asserting it, that lands here. It is a fact-table join on `amendment_of_id`

## Retrieval — both tools behind the shared signature

**Owner:**

- [ ] **Replace the INT-0 mock with the real tools, without breaking a test.** `mock/mock_retrieval_tools.py` and 47 conformance tests in `tests/test_contract.py` define the target exactly
- [ ] Keep `graphrag/contract.py` as the single definition of the evidence shape — if the real tools need a field the mock does not have, change the contract first
- [ ] Second hat: support fuzzy entity resolution, since name matching is a similarity problem

## Platform — stores, loaders, environments

**Owner:**

- [ ] Neo4j and DuckDB running locally and reproducibly, so nobody loses a day to setup
- [ ] **CI on this repo.** Data has `.github/workflows/`; we have 47 tests that nobody runs automatically
- [ ] Database connection once Co-lead 1's credentials land
- [ ] Implement whichever identity scheme Data agrees to — `uuid5` or the natural key — in the loaders

---

## Two things to know while allocating

**The W5 collision in the team model is worse than modelled.** It assumes extraction starts in W4. If it starts in W5 instead, it lands on the same week as the fact table, the query layer, two question sets and the spot-check, with three people wearing both hats at once. Starting extraction this week is the only thing that de-risks W5.

**Every stream is supposed to have two people.** Graph construction has Extraction 1 and Retrieval; Structured has Structured and Platform; Ontology has Extraction 2 and Co-lead 2. If a name appears in only one row, the pairing has been lost and one illness stops a workstream.
