# GraphRAG docs

**This directory is the live home for our contracts.** The Columbia Notion workspace is out of free blocks — paragraph and code-block edits work, but anything creating a block fails, and Notion rebuilds a table row when a cell is edited, so every table edit fails. Rather than maintain a page that is half-current, the Notion ontology page is now an archive pointing here. Change things in this directory.

| Document | What it is | Agreed with |
|---|---|---|
| [action-items.md](./action-items.md) | **Start here.** Five headings, not fifty-eight items | — |
| [assignments.md](./assignments.md) | Those items mapped onto the seven seats | — |
| [data-team-state.md](./data-team-state.md) | What Data has already built, and which asks to withdraw | Data |
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

## Code

| Path | What |
|---|---|
| `graphrag/contract.py` | Evidence shape and validation, mirroring RAG's `rag/contract.py` |
| `mock/mock_retrieval_tools.py` | INT-0 mocks for `graph_search` and `structured_search`. Standalone, standard library only |
| `tests/test_contract.py` | 47 conformance tests. They run against the mock now and the real tools later |

```bash
python3 -m pytest tests/ -q
```
