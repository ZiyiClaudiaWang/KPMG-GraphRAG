# Retrieval implementation status — 25 Oct 2026

This note records what can be completed on the `graphrag/data-access` branch **without
inventing interfaces that belong to other workstreams**.

## Completed now

### 1. Shared contract is the single source of truth for the INT-0 mock

`mock/mock_retrieval_tools.py` now imports validation constants, errors, ordering and
validation functions from `graphrag/contract.py` instead of maintaining a second copy.
The public mock behaviour is unchanged.

Acceptance check:

```text
pytest tests/test_contract.py -q
63 passed
```

### 2. Real retrieval wrapper exists without pretending a backend exists

`graphrag/retrieve.py` now exposes the final public signatures:

```python
graph_search(query, filters=None, top_k=10, trace_id=None)
structured_search(query, filters=None, top_k=10, trace_id=None)
```

The wrapper centralises:

- query / filter / top-k / trace validation;
- `RetrievalUnavailableError` semantics;
- trace propagation;
- deterministic score/tie ordering and rank assignment;
- final `Evidence` conformance validation.

A real graph or structured backend must be explicitly registered. Until one is
registered, the wrapper raises `RetrievalUnavailableError`; it never silently falls back
to mock or toy data. This preserves the contract distinction between **no evidence** and
**retrieval is unavailable**.

### 3. Conservative fuzzy entity-resolution baseline

`graphrag/entity_resolution.py` implements the reviewed rules in
`toy_graph/50_subsidiary_pairs_README.md`:

- punctuation/case normalisation;
- legal-form canonicalisation, including `N.A.` = `National Association`;
- hard block when both explicit legal forms disagree;
- optional hard block on explicit jurisdiction metadata;
- filing-defined aliases take precedence over similarity;
- conservative fuzzy similarity;
- three outcomes: `match`, `no_match`, `ambiguous`.

The resolver deliberately does **not** infer jurisdiction from entity-name text and does
not treat a missing legal suffix as proof of identity. Those choices avoid unsafe merges.

`resolve_segment_names()` exposes the same interface for Extraction 2, but segment-specific
canonical names / aliases and a labelled test set are not yet in this repository.

### 4. Reproducible evaluation script

Run:

```bash
python tools/evaluate_entity_resolution.py
```

against the 50-pair answer key. The current name-only baseline produces:

```text
Pairs: 50
Decisions: match=7, no_match=31, ambiguous=12
TP=7 FP=0 TN=29 FN=2
Precision=1.000
Recall=0.368
F1=0.538
Coverage=0.760
```

Interpretation: the baseline has **zero false merges** on the fixture, but intentionally
leaves many positive cases unresolved. The missing recall is not a reason to lower the
threshold blindly. Most unresolved positives need information the README explicitly says
should come first: filing-defined aliases, jurisdiction, or temporal/legal-event context.

The two hard false negatives are informative:

- `JPMSE` vs `J.P. Morgan SE` needs the filing-defined alias;
- Microsoft Regional Sales `Corporation` vs `Pte Ltd` is the documented re-domicile
  exception and needs event/temporal evidence.

## Test status

After these changes:

```text
pytest tests/ -q
112 passed
```

The original 63 conformance tests remain unchanged and all pass.

## What is deliberately not implemented yet

### Real `graph_search`

Blocked on the real graph retrieval backend. Sprint W6 says Kelly finishes E1 with five
retrievers against the real graph, and Claudia loads all three filings. This branch contains
extraction prototypes and saved results but not the production retriever/backend that the
shared wrapper should call.

Needed handoff:

1. Kelly/Claudia E1 retriever-plumbing branch/PR;
2. the real Neo4j Graph v1 / loader and connection/config contract;
3. mapping from each retriever's output to the shared `Evidence` fields, especially
   `section_id`, `quote`, `path`, `hops`, `edge_types`, `graph_version`,
   `extraction_version`, and `index_version`.

### Real `structured_search`

Blocked on Ashley's structured query layer / DuckDB fact backend. Retrieval should wrap
that backend rather than duplicate the fact-query logic.

Needed handoff:

1. callable interface for `lookup_fact` / comparison query layer;
2. fact result schema, including `fact_id`, concept, value, units, period, dimensions,
   `basis`, filing provenance and build/index version;
3. agreement on whether Retrieval or Structured owns final natural-language rendering of
   `Evidence.text`.

### Segment-name resolution

The resolver interface exists, but the repository has no segment counterpart to
`50_subsidiary_pairs.csv`.

Needed handoff from Extraction 2:

- canonical segment names and stable IDs scoped to company/filing period;
- filing-defined aliases/renames;
- a small labelled positive/negative/ambiguous validation set.

### Abstention contract

Not changed here because the W6 sprint explicitly assigns the envelope decision to
**Retrieval with Eval**. `[]` currently means no evidence and cannot safely be redefined by
one workstream.

The agreed design must distinguish at least:

1. search completed and found no evidence;
2. system deliberately refused/abstained;
3. two tied candidates were found and the system cannot choose.

Once Eval approves the shape, implementation order remains:

```text
contract.py -> mock -> tests -> real tools -> Agents/Eval integration
```

## Immediate dependency requests

The Retrieval owner can continue independently on tests/docs, but the next code-complete
milestones require these handoffs:

| Dependency | Needed for | Owner/source |
| --- | --- | --- |
| E1 real retriever plumbing | production `graph_search` | Kelly / Claudia |
| Neo4j Graph v1 + loader/config | production `graph_search` | Claudia / Platform |
| DuckDB structured-query backend | production `structured_search` | Ashley / Platform |
| Segment canonical set + labels | segment fuzzy resolution | Alina / Extraction 2 |
| Abstention envelope decision | abstention contract/tests | Eval + Retrieval |

PR #24 already removes a different blocker: `tools/graphrag_data.py` provides read-only
access to Data's filing and section text with provenance. That source is sufficient for
upstream text access; it does not replace the graph or fact retrieval backends above.
