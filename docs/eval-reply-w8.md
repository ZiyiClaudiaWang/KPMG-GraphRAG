# Reply to Eval, week 8

Written on 2026-11-09. Answers Eval's six requests and their corrections, from
`EVAL_GRAPHRAG.md` and the Week 6 one-pager in their repo.

Both of their documents are thorough, and the M17 and ADM checks saved us real work.

One framing point before the answers. We do not read the gold set as Eval's instrument for
grading each team. It is the shared set where Agents, Business/UI, RAG and GraphRAG meet,
and we would rather build it with them than be scored by it.

## 1 · Real retrieval tools — Wednesday 11 November

That is our only goal this week. Where it stands:

| Piece | State |
| --- | --- |
| Structured arm | on `main` |
| `graphrag/backends.py` — registers whichever arms the environment describes | PR #61, green |
| Graph arm | PR #57, open, awaiting review |

Once #61 and #57 land, switching off the mock is one import.

**There is a dependency Eval needs before pointing the harness at the real tools.** 148 of
the 155 questions in Gold Set v2 carry `form`, `fiscal_year` or `fiscal_period` in
`filters`. The real structured arm raises `ValueError: unsupported filter` on all three.
The mock accepts them and quietly ignores them, which is why the wiring run passed 174
calls with no errors. See [§ The filter blocker](#the-filter-blocker) below.

Do not switch `load_tools(mock=False)` until we confirm the fix, or round 1 fails on 95% of
the set for a reason that has nothing to do with retrieval quality.

## 2 · hops — two columns, not one

The contradiction Eval spotted is real. We think one number is being asked to do two jobs,
so our proposal is to split it:

- **`hops`** — edges traversed on the agreed ontology path, from the entity the question
  names to the evidence that answers it. This measures graph difficulty.
- **`filings`** — distinct accessions whose evidence is required. This measures what vector
  retrieval actually struggles with.

M17 is "four hops" under one reading and M18 "five each side" under the other, and both are
right about different things. Splitting them also keeps the point from
[eval-ask.md](./eval-ask.md): a question answerable from a single filing should not score
the same as one that made the join.

On Eval's correction that "nothing needs more than two hops" depends on the definition —
agreed, and under the edges reading almost nothing is under three, because our base path is
already `Company -FILES-> Filing -CONTAINS-> Section -SUPPORTS-> Claim`. That is a third
reason to keep the columns apart.

Once the definition is agreed we will derive both columns from the gold evidence and the
ontology path in code rather than filling them by hand, so the values are reproducible and
can be re-run when questions change. We will hand-fill the six relationship drafts first as
worked examples.

Small thing: `hops_v1.csv` holds 165 rows, not 106, and none are filled. Worth confirming
it is the sheet they want.

## 3 · The `search()` envelope

In scope with the real tools. Empty results become loggable at the same time.

## 4 · Extraction cost

A real run with tokens and dollars per filing is recorded in PR #47. It needs a rebase and
we will land it this week.

## 5 · The six relationship drafts

Reviewed. Four have paths our ontology supports today; two do not.

| Question | Graph path | Verdict |
| --- | --- | --- |
| EV1R-001 Exhibit 21 subsidiaries | `Filing -HAS_EXHIBIT-> Exhibit 21 -LISTS-> Subsidiary` | **Not yet.** Exhibit 21 is still PR #34; no `HAS_EXHIBIT` or `LISTS` edge exists on `main` |
| EV1R-002 segment change FY25 to FY26 | `ProductLine -RENAMED_FROM-> ProductLine` | **No.** We have no `RENAMED_FROM` edge and no ProductLine node. The segment half is answerable; the rename half is not |
| EV1R-003 OpenAI gains, event that drove it | `Event -AFFECTS-> Investment -REPORTED_IN-> ...` | Supported as a claim-level question. We hold the MD&A sentences; we do not model `Event` as a node, so this resolves by text, not traversal |
| EV1R-004 LinkedIn Ireland under Activision | absent edge | Supported, and a good test. The absence is real in our graph |
| EV1R-005 Microsoft acquired OpenAI | absent edge | Supported. Same shape as 004 |
| EV1R-006 Activision to segment revenue | `Exhibit 21 -LISTS-> Activision -PART_OF-> XBOX -IN_SEGMENT-> More Personal Computing` | **Partly.** Depends on Exhibit 21 (PR #34) for the first edge. See M17 below — the revenue direction in the draft is right, the attribution is not available |

## 6 · Data

Yes. Let us take the corpus merge and the FY2024 10-K for M05 to Data together.

## The mock findings — all three confirmed, and a fourth

Eval's three are real and all three are mock-only:

- `value_num: 82886` alongside `unit: "USD"` — the value is in millions, the unit is not
- two rows share `section_id: "sec-msft-26q3-note16"`
- segments are labels, not XBRL members

The fourth matters more for their adapter. The mock emits dimensions as a dict:

```json
{"StatementBusinessSegmentsAxis": "Intelligent Cloud"}
```

The real arm emits a list:

```json
[{"axis": "us-gaap:StatementBusinessSegmentsAxis",
  "member": "msft:IntelligentCloudMember",
  "type": "explicit"}]
```

`evidence_key()` passes that straight into `EvalKey(dims=...)`, so fact-level keys built
from mock output will not match keys built from real output.

## Corrections back

**NULLABLE_FIELDS.** `ticker` and `reporting_period_end` will be non-null on both arms.
**`section` will not.** The structured arm returns null for `section` and `section_id` on
every result, because an XBRL fact carries no section pointer. That is our open issue #40
and it will not close this week, so the snapshot fallback must not depend on `section`.

**M17.** The MD&A sentence is not in our corpus. "Activision" appears twice across both
Microsoft filings and both are the same Item 1A line:

> For example, in October 2023 we completed our acquisition of Activision Blizzard, Inc.

In FY2026 the segment moved the other way:

> Gaming revenue decreased $380 million or 7% driven by declines in Xbox content and
> services and Xbox hardware.

with Xbox content and services down 5% and Xbox hardware down 33%. The +$1,952M and +9.1%
belong to an earlier fiscal year. So M17 needs either the FY2024 filing — the same ask as
M05 — or rewording to the decline. This is the strongest single argument for the corpus
merge.

**M12.** We would rather reword it to the segment figure than keep it as a false premise.
Eval already has three false-premise and two fabricated-relationship questions, but M12 is
the only question that crosses a 10-K and its 10-K/A, and that amendment pair is one of the
clearest places a graph should beat a chunk index. Spending it on a refusal test loses the
only question that exercises it. If a false premise is wanted too, the elimination sign
flip is a good candidate for a separate one.

**M03 and M08.** Agreed, and turning M08 into an abstention question works. Keeping the
only relationship-ending question is worth more than the lookup.

**basicZ on EV1-052.** Not in anything we hold either. Likely the Doc.

**"53 of 100".** Thanks — we will use 62 exact-numeric, 71 numeric or comparative.

## How the two arms retrieve

They do not share a source, and the difference matters for the harness.

- **Structured arm** reads XBRL facts. Today from a DuckDB store our loader builds; a second
  loader now reads the Data team's Postgres directly, so the source can change without
  touching the query path.
- **Graph arm** searches a Neo4j graph built from filing *section text*. Our extraction
  pipeline turns Data's section text into Claim nodes linked to Company, Filing, Section and
  RiskFactor. It does not read XBRL facts.

Numbers come from XBRL, prose comes from a text-derived graph.

One consequence for the side-by-side. The graph arm has 696 claims for the Microsoft 10-Q
used in Week 8, and all 20 filings are loaded — 10,774 claims in total. It was left out of
Part A because we had not got Eval credentials, not because it had no data. Worth re-running
Part A with it in.

## The filter blocker

The contract in `graphrag/contract.py` allows thirteen filter keys for the structured arm:

```
ticker  cik  form  accession_number  filing_date  reporting_period_end
fiscal_year  fiscal_period  section  is_table  concept  segment  basis
```

`structured/structured_query_v1.py` accepts seven of them:

```
ticker  cik  accession_number  concept  reporting_period_end  segment  basis
```

The six missing keys are not ignored. Anything outside the accepted set raises:

```python
raise ValueError(f"unsupported filter: {key}")
```

Counted against Gold Set v2:

| Filter key | Questions using it | Real arm |
| --- | --- | --- |
| `ticker` | 155 | accepted |
| `form` | 145 | raises |
| `fiscal_year` | 129 | raises |
| `fiscal_period` | 127 | raises |

**148 of 155 questions carry at least one key the real arm refuses.**

Nobody has hit this because the mock accepts all thirteen: `SHARED_FILTERS` in the contract
includes them, and `_matches()` skips `fiscal_year`, `fiscal_period` and `is_table` rather
than filtering on them. So the mock tolerates exactly the filters the real code rejects, and
every downstream team has been building against the mock.

Two ways to fix it:

1. **The structured arm accepts the contract's keys.** `form` and `filing_date` become real
   `WHERE` clauses against `filings_v1`, which already holds both. `fiscal_year`,
   `fiscal_period`, `section` and `is_table` are accepted and ignored, as the mock does,
   because a fact has no section and we do not store a fiscal calendar.
2. **The contract drops them** and Eval regenerates `filters` across the gold set.

We prefer the first. It is a small diff on our side, `form` is genuinely useful for filtering
a 10-K from a 10-Q, and it does not ask Eval to rewrite 148 questions.
