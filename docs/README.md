# GraphRAG docs

Ported from Notion on 2026-10-13. **The Columbia Notion workspace has used all its free blocks**, so new pages and new blocks cannot be created there and even table-cell edits fail. This directory is now the editable home for our contracts. Notion stays readable as a snapshot; change things here.

| Document | What it is | Agreed with |
|---|---|---|
| [action-items.md](./action-items.md) | **Start here.** Five headings, not fifty-eight items | — |
| [data-team-state.md](./data-team-state.md) | What Data has already built, and which asks to withdraw | Data |
| [data-contract.md](./data-contract.md) | What we need from the Data team — three layers, record schemas, semantics | Data |
| [architecture.md](./architecture.md) | How the pieces fit, the tool signature and the evidence envelope | Agents, RAG |
| [schema-ids-operations.md](./schema-ids-operations.md) | **Canonical.** Ontology, deterministic keys, provenance shapes, operations, question routing | Data, RAG, Agents |
| [interface-rag-gap.md](./interface-rag-gap.md) | Our envelope vs RAG's shipped v0 contract, and what to do about it | — |
| [eval-ask.md](./eval-ask.md) | The multi-hop gold-set tranche we need from Eval, with candidate questions | Eval, RAG |
| [section-vocabulary.md](./section-vocabulary.md) | Why one Item-level label isn't enough, and the three-field fix | Data, RAG, Eval |
| [agent-notes.md](./agent-notes.md) | What changes for Agents now that we conform to RAG's contract | Agents, UI |
| [ontology-proposal-claudia.md](./ontology-proposal-claudia.md) | A proposal against the standing schema. Adds the Claim node with claim_type, extraction_status, and two business use cases | — |
| [ontology-reconciliation.md](./ontology-reconciliation.md) | **Read before the session.** Where the two differ, a strawman for v1, and eight decisions — each needing a question it unlocks | — |
| [sprints/](./sprints/) | One file per week: owners, done conditions, decisions the week is waiting on | — |

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
| `toy_graph/` | A small graph in Neo4j, used to test the schema against a question that needs several hops. Found two gaps — see its README |

```bash
python3 -m pytest tests/ -q
```

```bash
cd toy_graph && python3 toy_graph.py && python3 check_findings.py
```
