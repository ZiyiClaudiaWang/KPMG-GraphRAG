# CW draft ontology — executable schema probe

This is the implementation companion to the proposed decisions in
[ontology-reconciliation.md](../docs/ontology-reconciliation.md). It is pending
team review, not a jointly signed-off ontology or a real extraction pipeline.

The probe has **26 nodes, 30 relationships and eight node types**: Company,
Filing, Section, Segment, Subsidiary, Fact, Claim and RiskFactor.

## Sources and scope

- The Northwind 10-K and amendment are **constructed fixtures**, retained to
  reproduce the subsidiary-to-segment and fact-supersession findings.
- Six Apple FY2025 Item 1A sentences are manually selected annotations from
  [claim_examples.md](claim_examples.md). The script reads this file; it does not
  use an LLM to extract statements from a filing.
- Claim-to-theme assignments and the five-theme vocabulary are **new candidate
  annotations requiring review**. An international-sales fact is tagged as
  international exposure; it is not itself evidence that a loss occurred.
- Document, Table, Cell, nested sections, fuzzy entity resolution, real amendment
  ingestion, automatic extraction and the production evidence API are not tested.
- The earlier committed PNGs show the historical 11-node probe. Regenerate them
  to obtain this draft's views; they are not evidence that the new loader ran.

## Files

| File | Purpose |
| --- | --- |
| `toy_graph.py` | Definitions, IDs, scoped loader, repeated-load/re-chunk checks |
| `check_findings.py` | Historical failures and corrected results, plus Claim paths |
| `claim_examples.md` | Six source sentences, proposed labels and interpretation notes |
| `render.py` | Optional diagrams read back from the live probe |
| `.env.example` | Connection template; never commit your actual `.env` |

## Run

From the repository root, without a database or third-party packages:

```bash
python toy_graph/toy_graph.py --check-only
```

For live checks, use a Neo4j 5.26 development database. For example, if Docker is
installed:

```bash
docker run -d --name kpmg-graph -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/sketchpass neo4j:5.26
```

Then:

```bash
pip install -r toy_graph/requirements.txt
cp toy_graph/.env.example toy_graph/.env
```

Edit `.env` for your database, using `NEO4J_URI`, `NEO4J_USER` and
`NEO4J_PASSWORD`. A remote Sandbox/Aura connection works through the same driver.
The script explicitly loads `.env` from its own directory. Environment variables
take precedence.

```bash
python toy_graph/toy_graph.py
python toy_graph/check_findings.py
python -m pytest tests/ -q
```

Expected loader output:

```text
Fixture valid: 26 nodes, 30 relationships; 8 node types
Repeated load: (26, 30) — idempotent
Simulated re-chunk: (26, 30) — no duplicates
```

A mismatch raises an error instead of printing success. Running
`python toy_graph/toy_graph.py --reset` deletes only nodes tagged with this
probe's `probe_id`, then reloads them. The default run does not delete data.
Queries and counts are scoped to the probe; unrelated nodes do not inflate counts.
Composite uniqueness uses `(probe_id, key)`. If your database already enforces a
conflicting global key constraint, use a separate development database rather
than removing that constraint.

In Neo4j Browser, display the probe with:

```cypher
MATCH (n {probe_id: 'cw-ontology-draft-20261016'})
OPTIONAL MATCH (n)-[r]->(m {probe_id: 'cw-ontology-draft-20261016'})
RETURN n, r, m
```

## What the checks establish

1. Parent-only traversal returns two candidate segments; explicit
   `CONSOLIDATED_INTO` returns Freight Services with a quote and stable provenance.
2. Filing-level supersession wrongly invalidates FY2023. An explicit fixture
   replacement mapping preserves FY2023 and selects the corrected FY2024 value.
   The illustrative comparison is **35.6%**, versus 43.8% with the old value.
3. Six Company–Filing–Section–Claim–RiskFactor paths exist. The reference-dependent
   sentence retains context; the global prediction retains its scope and mixed
   content note. Review status remains `needs_review`.
4. Loading twice and changing chunk attributes yields the same node and edge
   counts. Text relationship keys include accession and section, never chunk ID.
5. Numeric values are absent from Neo4j Fact nodes. `FACT_VALUES` is a local fixture
   lookup standing in for Structured's future store, **not a DuckDB implementation**.
   `FACT_REPLACEMENTS` is explicit constructed evidence; matching concept/period
   or merely having a 10-K/A does not establish replacement.

Local probe keys are clearly distinguished from Data's production identifiers.
Apple's section uses a local section key; Data's section ID and XPath are still
pending. Chunk IDs are opaque and never parsed. Identical sentences within the
same section deduplicate in this prototype; separate repeated occurrences will
need stable occurrence locators before scale-up. `Fact` remains a graph pointer,
with numeric values obtained outside Neo4j.

## Optional diagrams

```bash
python toy_graph/render.py
```

This writes four updated finding views and a fifth Claim-extension view to
`toy_graph/images/`. All views are draft illustrations, not a real-filing
extraction benchmark.
