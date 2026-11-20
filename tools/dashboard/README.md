# Corpus dashboard — running it yourself

What is loaded in each arm, whether it holds together, and how a question ranks.
Everything it shows comes from `tools/corpus_report.py`, so the figures here, the
ones pytest asserts on and the ones the deck prints are the same figures.

**Team-local.** Everyone runs it against their own environment. There is no hosting
and no shared credentials, and if it ever has to face a client that is a separate
decision with a separate answer on credentials.

## First run

```bash
cp extraction_pipeline/.env.example extraction_pipeline/.env.internal  # then fill it in — see Credentials below
pip install -r requirements-dev.txt  # streamlit and pandas are in there
python3 tools/corpus_report.py       # check the connection before Streamlit
streamlit run tools/dashboard/app.py
```

Run the report before the dashboard. If credentials are wrong you get one legible
error on the terminal instead of a stack trace inside a web page.

## Credentials

Nothing here is in the repo and nothing here should ever be pasted into a commit, a
PR, a Notion page or a transcript. Ask a co-lead and they will send them out of band.

Two graphs, and knowing which one you are looking at is most of the value:

| Instance | What it is | Credentials file |
| --- | --- | --- |
| `internal` | the team's own graph, the default, where we look first | `extraction_pipeline/.env.internal` |
| `canonical` | what Eval and Agents are given | `extraction_pipeline/.env.canonical` |

Both files live in `extraction_pipeline/`, next to the pipeline's own `.env`, but the
dashboard never reads `extraction_pipeline/.env`. That file belongs to the extraction
pipeline (`run_pipeline.py`) and can point wherever the pipeline run needs. The
dashboard only reads `.env.internal` and `.env.canonical`, so changing `.env` does
not change what the dashboard shows. All three files are git-ignored.

They are not copies of each other. As of 16 Nov `internal` has the Exhibit 21
subsidiaries loaded and `canonical` does not, so a subsidiary question answers on one
and returns nothing on the other. The sidebar radio switches between them; the
provenance block under the title always says which one you are on.

`extraction_pipeline/.env.internal` needs `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`,
`NEO4J_DATABASE` and `MSFT_FACT_DB_PATH`. `NEO4J_DATABASE` is the instance ID, not
`neo4j` — that one has cost two people an afternoon each.

## The fact store

`MSFT_FACT_DB_PATH` is an absolute path, no `~`, to a DuckDB file that is **not in the
repo** and is not downloadable from anywhere. Ask for a copy, or build one with
`tools/build_fact_store.py`.

Two older stores are still on various laptops and both are wrong to use now:

- the **EDGAR** store, deprecated 13 Nov
- the **tracked** store from #55, which holds 1 filing and 1,429 facts and raises on
  most structured queries

The store meta line in the provenance block tells you which one you have opened.
The current one says `corpus_version=data-api-raw:20 · source=data-api:raw`, and it
says `source is the UNVALIDATED set, not the validated view` because that is true and
worth seeing every time.

## What you should see, 16 Nov 2026

If your numbers differ from these, your environment differs — that is the point of
listing them.

**Graph, `internal`** — scope `kelly-extraction-pilot-v1`

| | |
| --- | --- |
| Claim | 10,774 |
| Subsidiary | 752 |
| Section | 48 |
| Filing | 20 |
| Company | 10 |
| RiskFactor | 10 |

Edges: `SUPPORTS` 10,774 · `CONCERNS_RISK` 6,198 · `OWNS` 752 · `CONTAINS` 48 ·
`FILES` 20. Subsidiary coverage 10 of 10 10-Ks. No orphans.

**Graph, `canonical`** — the same, minus `Subsidiary` and `OWNS` entirely. Coverage
reads 0 of 10 and the driver warns that the label does not exist. That is the
difference between the two instances, not a broken setup.

**Structured arm** — 20 filings · 10 companies · 24,533 facts · 2,063 concepts.

**Checks: 5 of 19 want a look**, and these are the five, so a flag is only news if it
is a *different* flag.

| Check | Today | |
| --- | --- | --- |
| columns entirely NULL | `scale`, `sign`, `format` | an all-null column gets no type |
| parseable numbers left empty | 187 | `raw_value` holds a number, `numeric_value` does not |
| filings missing a key concept | 6 | 2 with no net income, 4 with no operating income |
| comparatives that disagree on value | 32 | a restatement, or two loaders disagreeing. Nobody has looked |
| quarterly filing over a year | 2 | a period that cannot be right for its form |

## Three things that look like bugs and are not

- **The first read after an idle period takes a few seconds.** Aura cold-starts.
  Nothing re-queries the graph until you press **Refresh** — that is deliberate, so a
  page opened in the morning does not report retrieval as down.
- **The `rag` column in Cross arm is empty.** RAG's index lives in another repo. It
  is empty by choice, not by failure.
- **The oracle checks are missing** — accounting identity, segment sums, quarterly
  roll-up. Not built yet. All three fail today against known bugs, so they are worth
  watching go green rather than shipping red.

## The three tabs

**Corpus** — what is in each arm, per filing, with the provenance of where it came
from. Totals hide a filing that loaded thin, so the per-filing table is the one to
read. The thinnest-filings list is at the bottom for the same reason.

**Checks** — the things that should be true, each one saying what it catches if it is
not. `pass` and `look`, never `fail`: several of these are expected to be non-zero
and the column is there to be read, not to go green.

**Ask** — run a question through both arms and compare the ranking. Every run appends
to `data/dashboard/query_log.jsonl` with the question, filters, status, latency,
trace ID and the provenance key of each row. That log is how the period-word bug and
the street-address bug were both found, and it is how a fix gets *shown* to have
changed the ranking rather than asserted to have.

## If it will not start

| What you see | What it means |
| --- | --- |
| `No NEO4J_URI in …/extraction_pipeline/.env.internal` | The file is missing or empty. Copy `extraction_pipeline/.env.example` and fill it in |
| `No credentials at extraction_pipeline/.env.internal` in the sidebar | Same, caught earlier |
| `No store configured. Set MSFT_FACT_DB_PATH` | The path is unset, relative, or uses `~` |
| `RetrievalUnavailableError` on the first Ask | Aura cold start. Press Refresh once and ask again |
| Counts roughly double | A second extraction run is in the database. The report warns when more than one `pipeline_scope` is present — set one in the sidebar |
