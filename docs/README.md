# GraphRAG docs

Ported from Notion on 2026-10-13. **The Columbia Notion workspace has used all its free blocks**, so new pages and new blocks cannot be created there and even table-cell edits fail. This directory is now the editable home for our contracts. Notion stays readable as a snapshot; change things here.

| Document | What it is | Agreed with |
|---|---|---|
| [data-contract.md](./data-contract.md) | What we need from the Data team — three layers, record schemas, semantics | Data |
| [architecture.md](./architecture.md) | How the pieces fit, the tool signature and the evidence envelope | Agents, RAG |
| [schema-ids-operations.md](./schema-ids-operations.md) | **Canonical.** Ontology, deterministic keys, provenance shapes, operations, question routing | Data, RAG, Agents |
| [interface-rag-gap.md](./interface-rag-gap.md) | Our envelope vs RAG's shipped v0 contract, and what to do about it | — |
| [eval-ask.md](./eval-ask.md) | The multi-hop gold-set tranche we need from Eval | Eval, RAG |

## Open items, most urgent first

1. **Reconcile the evidence envelope with RAG's v0.** Their `retrieval_api.md` on `Rachel-xs/KPMG_RAG@contract-v0` is final and has tests, three other consumers, and different field names from ours. See [interface-rag-gap.md](./interface-rag-gap.md).
2. **Ask Eval for multi-hop coverage.** The current gold set is one 10-Q, so nothing in it measures the graph arm. See [eval-ask.md](./eval-ask.md).
3. **Four additions to the data contract** not yet raised with Data: `basis`, table-cell provenance, notes-are-not-Items, and keeping the gold set out of the corpus.
4. **Fix the two Layer 0 key problems** — the section key is offset-dependent, and `section_id` has two competing definitions.

## Other team repos

| Team | Repo | Notes |
|---|---|---|
| Data | `michaelliruoxi/data_engineering_kpmg` | AWS RDS Postgres, not the file drop our contract assumes |
| RAG | `Rachel-xs/KPMG_RAG` | Contract and working retriever live on branch `contract-v0`, not `main` |
