# The graph backend

`graph_backend.py` is the thing `retrieve.configure_graph_backend()` was written
for. Before it, every call to `graph_search` raised
`RetrievalUnavailableError`, because nothing outside a test fixture registered
a backend.

## What it searches

The graph the extraction pipeline writes:

```
(Company)-[:FILES]->(Filing)-[:CONTAINS]->(Section)-[:SUPPORTS]->(Claim)
```

A Claim holds the sentence, its type and its subject. The company, form,
accession and filing date live further up the path. The evidence envelope
needs all of them and will not accept a null for most, so every search walks
all four nodes. That is why the traversal is three hops and not one, and why
`max_hops` below 3 is refused rather than quietly served.

Matching is a Neo4j full-text index over `Claim.quote`. Neo4j scores the match
and that score is what the envelope reports.

## Running it

Load a filing with the extraction pipeline's step 8, then:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=sketchpass

python3 -c "
from graphrag import graph_backend, retrieve
graph_backend.ensure_index()
retrieve.configure_graph_backend(graph_backend.search)
for r in retrieve.graph_search('competition', top_k=3, trace_id='demo'):
    print(r['rank'], r['metadata']['claim_type'], '|', r['quote'][:70])
"
```

`ensure_index()` is safe to call every time. Skip it and the search returns
nothing at all, which reads as 'no evidence' rather than 'not indexed'.

## Filters

Served, because the graph stores them:

`ticker`, `cik`, `form`, `accession_number`, `filing_date`,
`reporting_period_end`, `section` (matched against `section_key`).

Refused by name, with a reason:

| filter | why |
| --- | --- |
| `fiscal_year` | the graph stores `reporting_period_end`, and a fiscal year cannot be read off it for every filer |
| `fiscal_period` | the graph stores the form, not the quarter |
| `is_table` | Claims come from prose only, so the answer is the same for every row |
| `max_hops` below 3 | fewer hops than the envelope needs |

A refused filter raises `contract.FilterNotSupportedError`. It is deliberately
not `RetrievalUnavailableError`: an outage is worth retrying and should score
as infrastructure noise, while a filter this graph does not hold never becomes
serveable. `retrieve._run` used to collapse the two, so both arrived as
outages.

Three answers that must stay distinct:

- **evidence** — rows came back
- **no evidence** — `[]`, the query ran and matched nothing
- **cannot** — `FilterNotSupportedError`, the query was never run
- **unavailable** — `RetrievalUnavailableError`, the store could not be reached

## What a result carries

`section_id` is the provenance key and `chunk_id` is always null, which is the
contract's rule for our arms.

`metadata` carries two fields retrieval could not see before (issue #51):

- `claim_type` — `stated_fact`, `forward_looking` or `hedged_risk`
- `subject_scope` — what the statement is about when it is not the filer

They are in `metadata` rather than `EVIDENCE_FIELDS` so the structured arm is
not forced to return a null scope for a number. On the FY2026 run, a search for
"competitors competition rivals" returns 13 of 20 rows about someone other than
Microsoft; without scope an agent can fairly read those as Microsoft describing
itself.

`metadata.raw_spans` holds character offsets into Data's section content. They
are section-relative and counted in Unicode codepoints, not bytes. Slicing
Data's `content[start:end]` returns the quote exactly — checked on the FY2026
run, 10 of 10 exact.

The loader writes spans as JSON text because Neo4j cannot store a nested list.
This module parses them back, so callers get a list.

`metadata.source_unit_ids` points back at the units a Claim came from. The
extraction pipeline is partway through renaming these from `sentence_ids` to
`context_unit_ids`, so the query reads both and returns whichever the loader
wrote. This matters more than it looks: Cypher returns null for a property
that is not there rather than failing, so reading only one name would lose
the pointer without anything breaking. The driver also has `UNRECOGNIZED`
notifications turned off, because one of each renamed pair is always absent
and Neo4j warns about it on every query.

Three Item 1A fields ride along when the newer loader wrote them and are null
otherwise: `risk_lead_unit_id`, `risk_lead_sentence` and `risk_summary`.

`source_url` is built from the CIK and accession rather than stored, because
the envelope requires a non-null URL and the graph holds no such property.

## Tests

`tests/test_graph_backend.py`. Everything runs on a bare checkout; the two
`test_live_*` tests skip themselves unless `NEO4J_URI` points at a database
holding a loaded extraction run. The check walks the full path on purpose —
the toy graph also has Claim nodes, and running these against toy data would
fail for the wrong reason.
