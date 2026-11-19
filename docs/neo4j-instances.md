# Which Neo4j instance is which

18 November 2026.

There are two Aura instances and nothing in this repository said which was
which. The distinction lived in the name of a local dotenv file, so a claim
about one of them could not be checked by anyone reading the repo. That is how
the two came to be written down the wrong way round in the W9 plan.

**No instance id or credential belongs in this file.** This repository is
public, and on Aura the instance id is also the username. Both instances are
identified below by their Aura display name and by the dotenv file that holds
their settings. Credentials go to another team out of band, never through the
repo and never through a transcript.

## The two

| Aura display name | Settings live in | Role |
| --- | --- | --- |
| `KPMG GraphRAG` | `extraction_pipeline/.env` | The one Eval and Agents are given. Score against this. |
| `KPMG - GraphRAG - 2` | `.env.internal` | Ours. A second copy to load into without disturbing the first. |

## What they held, measured 18 November

Both, identically: 10,774 `Claim`, 752 `Subsidiary`, and the labels `Company`,
`Filing`, `Section`, `Claim`, `RiskFactor`, `Subsidiary`.

The Exhibit 21 load has run on both. The carried-over item in the W9 plan
saying the shared instance has no `Subsidiary` label is out of date.

They differ in one way, and it is not the data.

## A loaded graph is not a working one

`KPMG - GraphRAG - 2` has no `claim_quote_fulltext` index, so **every**
`graph_search` call against it raises:

    There is no such fulltext schema index: claim_quote_fulltext

`graph_search` reaches claims through `db.index.fulltext.queryNodes`, and that
index is also what produces the `score` the evidence envelope reports. Loading
the graph does not create it — `graph_backend.ensure_index()` does, once per
instance.

So an instance can hold a complete and correct graph and still fail every
question. Check the index, not just the node counts:

```python
from graphrag import graph_backend
graph_backend.ensure_index()   # safe to run again; IF NOT EXISTS
```

## What neither instance can do

The data being present is not the same as the arm being able to reach it.
`graph_search` runs one pattern:

```cypher
CALL db.index.fulltext.queryNodes($index, $lucene, {limit: $limit})
YIELD node AS c, score
MATCH (co:Company)-[:FILES]->(fl:Filing)-[:CONTAINS]->(sec:Section)-[sup:SUPPORTS]->(c)
```

The claim arrives from the full-text index. The three hops above it exist to
fill the envelope — company, form, accession, filing date — not to answer
anything. The arm never walks `OWNS` and never walks `CONCERNS_RISK`.

So the nine Exhibit 21 questions fail for a retrieval reason, not a data one.
Asking which subsidiaries a company owns returns sentences that mention a
subsidiary, which is not the relationship. That gap is the case for the
generated-Cypher arm in [text2cypher-scope.md](./text2cypher-scope.md).

## Before any demo

Wake the instance first. The first call after it has been idle raises
`RetrievalUnavailableError: Unable to retrieve routing information`, which is
Aura starting up rather than an outage, and it reads to a room as the system
being down.

```python
graph_backend.connect().verify_connectivity()
```
