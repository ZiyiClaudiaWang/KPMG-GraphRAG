# GraphRAG docs

Ported from Notion on 2026-10-13. **The Columbia Notion workspace has used all its free blocks**, so new pages and new blocks cannot be created there and even table-cell edits fail. This directory is now the editable home for our contracts. Notion stays readable as a snapshot; change things here.

| Document | What it is | Agreed with |
|---|---|---|
| [action-items.md](./action-items.md) | **Start here.** Everything open, by counterparty | — |
| [data-contract.md](./data-contract.md) | What we need from the Data team — three layers, record schemas, semantics | Data |
| [architecture.md](./architecture.md) | How the pieces fit, the tool signature and the evidence envelope | Agents, RAG |
| [schema-ids-operations.md](./schema-ids-operations.md) | **Canonical.** Ontology, deterministic keys, provenance shapes, operations, question routing | Data, RAG, Agents |
| [interface-rag-gap.md](./interface-rag-gap.md) | Our envelope vs RAG's shipped v0 contract, and what to do about it | — |
| [eval-ask.md](./eval-ask.md) | The multi-hop gold-set tranche we need from Eval, with candidate questions | Eval, RAG |
| [section-vocabulary.md](./section-vocabulary.md) | Why one Item-level label isn't enough, and the three-field fix | Data, RAG, Eval |
| [agent-notes.md](./agent-notes.md) | What changes for Agents now that we conform to RAG's contract | Agents, UI |

## Open items

Moved to [action-items.md](./action-items.md), which consolidates everything open across Data, RAG, Eval, Agents and our own schema.

## Other team repos

| Team | Repo | Notes |
|---|---|---|
| Data | `michaelliruoxi/data_engineering_kpmg` | AWS RDS Postgres, not the file drop our contract assumes |
| RAG | `Rachel-xs/KPMG_RAG` | Contract and working retriever live on branch `contract-v0`, not `main` |
