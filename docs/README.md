# GraphRAG docs

Ported from Notion on 2026-10-13. **The Columbia Notion workspace has used all its free blocks**, so new pages and new blocks cannot be created there and even table-cell edits fail. This directory is now the editable home for our contracts. Notion stays readable as a snapshot; change things here.

| Document | What it is | Agreed with |
|---|---|---|
| [data-contract.md](./data-contract.md) | What we need from the Data team — three layers, record schemas, semantics | Data |
| [architecture.md](./architecture.md) | How the pieces fit, the tool signature and the evidence envelope | Agents, RAG |
| [schema-ids-operations.md](./schema-ids-operations.md) | **Canonical.** Ontology, deterministic keys, provenance shapes, operations, question routing | Data, RAG, Agents |
| [interface-rag-gap.md](./interface-rag-gap.md) | Our envelope vs RAG's shipped v0 contract, and what to do about it | — |
| [eval-ask.md](./eval-ask.md) | The multi-hop gold-set tranche we need from Eval, with candidate questions | Eval, RAG |
| [section-vocabulary.md](./section-vocabulary.md) | Why one Item-level label isn't enough, and the three-field fix | Data, RAG, Eval |
| [agent-notes.md](./agent-notes.md) | What changes for Agents now that we conform to RAG's contract | Agents, UI |

## Open items, most urgent first

1. **Get the two additive fields agreed with RAG** — `section_id` and `quote`. We have adopted their names everywhere else; these two are the only things we cannot get from their schema. Eight further reconciliation items are listed in [interface-rag-gap.md](./interface-rag-gap.md), of which the unowned structured-tool contract and the Notes-are-not-Items section vocabulary are the two that block work.
2. **Ask Eval for multi-hop coverage.** The current gold set is one 10-Q, so nothing in it measures the graph arm. See [eval-ask.md](./eval-ask.md).
3. **Take the section-vocabulary question to Data, RAG and Eval together.** Segments and subsidiaries both sit below Item level, so a single Item-level label cannot reach them. See [section-vocabulary.md](./section-vocabulary.md).
4. **Re-send the data contract.** It now uses RAG's vocabulary end to end and adds `basis`, table-cell provenance, sub-Item sections and the `extraction_version` / `chunking_version` split.
5. **Fix the two Layer 0 key problems** — the section key is offset-dependent, and `section_id` has two competing definitions. Proposed: Data's IDs are canonical and ours are deleted.

## Other team repos

| Team | Repo | Notes |
|---|---|---|
| Data | `michaelliruoxi/data_engineering_kpmg` | AWS RDS Postgres, not the file drop our contract assumes |
| RAG | `Rachel-xs/KPMG_RAG` | Contract and working retriever live on branch `contract-v0`, not `main` |
