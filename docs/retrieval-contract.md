# Retrieval contract

What the two arms take, what they return, and what each field means. This is the reference; [mcp-endpoint.md](./mcp-endpoint.md) is how to run the server.

The same contract holds whether you call over MCP or import `graphrag.backends` — same arguments, same envelope. Every field below is checked by `graphrag/contract.py` on the way out, so an arm that returns something else raises rather than passing it on.

## Contents

- [The tools](#the-tools)
- [Arguments](#arguments)
- [Filters: what each arm does with each key](#filters-what-each-arm-does-with-each-key)
- [The response](#the-response)
- [The decision: status and reason](#the-decision-status-and-reason)
- [Evidence fields](#evidence-fields)
- [`metadata`, by arm](#metadata-by-arm)
- [A call and its answer, over the wire](#a-call-and-its-answer-over-the-wire)
- [The published schemas](#the-published-schemas)
- [Errors](#errors)
- [Versions on every answer](#versions-on-every-answer)

## The tools

| Tool | Searches | Ask it |
|------|----------|--------|
| `graph_search(query, filters, top_k, trace_id)` | Claims extracted from filing prose, over a Neo4j full-text index on the claim sentence | What a company **said** — risks, commitments, descriptions |
| `structured_search(query, filters, top_k, trace_id)` | An XBRL fact table in DuckDB, matched on concept and dimensions | What a company **reported** — a tagged number with its period |
| `describe_filters()` | Nothing; it reports what the two arms do with each filter key | Before you send filters |

Both return evidence, never an answer. `cypher_search` — generated Cypher — exists as a seam and is not served: it raises until a backend registers, and a tool that always fails is worse than an absent one.

## Arguments

| Argument | Type | Default | Meaning |
|----------|------|---------|---------|
| `query` | string | required | The question or phrase. Non-empty; it is matched, never parsed for filters |
| `filters` | object | `null` | Keys from the table below. An unknown key raises rather than widening the search. A key set to `null` is ignored, so you can always send the full dict |
| `top_k` | integer | `10` | 1 to 50. The arm sorts before it truncates, so `top_k` changes how many you see, not which are best |
| `trace_id` | string | `null` | Opaque to us. Echoed back on every row at `metadata.trace_id`, always present, null when unset. Eval join on it |

## Filters: what each arm does with each key

**`applied` reaches the query and narrows the result. `ignored` is accepted and never read. `refused` raises by name.** Only the first does what you expect, and an ignored key returns unfiltered rows that look exactly like filtered ones — which is why this table exists. Read it at runtime from `describe_filters()`; it is derived from the arms themselves and cannot disagree with them.

| Key | Graph | Structured | Meaning |
|-----|-------|------------|---------|
| `ticker` | applied | applied | `MSFT`. A list is allowed and means any of them |
| `cik` | applied | applied | Ten digits, zero-padded |
| `form` | applied | applied | `10-K`, `10-Q` |
| `accession_number` | applied | applied | One filing |
| `filing_date` | applied | applied | The date the filing was submitted |
| `reporting_period_end` | applied | applied | The date the period ends |
| `fiscal_year` | applied | applied | The filer's own fiscal year, derived per filer from its filings — **not** the calendar year. See the note below |
| `fiscal_period` | applied | applied | `Q1`–`Q4` or `FY`. Needs `fiscal_year` beside it: a quarter alone names no period |
| `section` | applied | **ignored** | ⚠️ The graph arm matches **only the slug**, `part-i-item-1a`. `Part I Item 1A` returns nothing and abstains (#134). The fact table has no section pointer at all (#40) |
| `is_table` | **refused** | **ignored** | Claims come from prose only, so every claim would match `false` and none would match `true` |
| `concept` | — | applied | An XBRL tag, e.g. `us-gaap:Assets` |
| `segment` | — | applied | A reportable segment |
| `basis` | — | applied | `as_filed` |
| `max_hops` | **refused** | — | The graph walks a fixed three hops upward to fill the envelope. The depth is structural, not a setting |

⚠️ **Fiscal year is the filer's, not the calendar's.** We derive each filer's calendar from its own filings: a 350–380 day period is the fiscal year, labelled by the calendar year it **ends** in; a quarter is its offset from the year start. Don't translate `fiscal_year` into dates yourself — for a January year-end filer, "a 2026 filing is FY2026" is wrong by a full year.

## The response

| Field | Meaning |
|-------|---------|
| `request_id` | One call. Generated if you don't send one; use it when you report a problem |
| `status` | `answer`, `abstain` or `clarify`. See below |
| `reason` | Why, when the status is not `answer`; `null` when it is |
| `results` | The evidence, highest ranked first. Empty unless the status is `answer` |
| `count` | `len(results)`, so you don't have to |
| `arm` | `graph` or `structured`, so a composed answer can say where each piece came from |
| `graphrag_commit` | The commit that served the call |

## The decision: status and reason

An empty `results` on its own cannot tell you whether the arm looked and found nothing or declined to look. This can. An `answer` always carries evidence and never a reason; everything else carries a reason and no evidence.

| `status` | `reason` | Meaning |
|----------|----------|---------|
| `answer` | `null` | Evidence found. Compose from it |
| `abstain` | `missing_fact` | The arm looked and the corpus does not hold it |
| `abstain` | `out_of_scope` | Not the kind of question this corpus answers |
| `abstain` | `false_premise` | The question assumes something the filings contradict |
| `abstain` | `fabricated_relationship` | It asserts a link that does not exist |
| `clarify` | `ambiguous` | More than one reading; ask which |

Richer non-score triggers are in review (#86) and land on this same seam, so they will appear here without the response shape changing.

## Evidence fields

One row per piece of evidence, nineteen fields, the same nineteen from both arms. "Filled by" says which arm populates it; a field a given arm cannot know is `null` rather than absent, so the shape never changes.

| Field | Filled by | Meaning |
|-------|-----------|---------|
| `rank` | both | 1 is the best match in this call. Rank is comparable inside one call and nowhere else |
| `score` | both | Raw and unnormalised. **Never threshold it, never compare it between arms** — a graph score and a fact score mean nothing to each other. Use the order |
| `text` | both | What was retrieved: the claim sentence, or a line describing the fact |
| `quote` | graph | The filer's own sentence, exactly as published. `null` on the structured arm |
| `chunk_id` | RAG's arm | Always `null` from us. It is in the envelope because all three arms share it |
| `section_id` | graph | The section a claim came from. `null` on the structured arm |
| `section` | graph | The section slug, `part-i-item-1a`. `null` on the structured arm |
| `ticker` | both | `MSFT`. A filter may send one or a list |
| `cik` | both | Ten digits, zero-padded |
| `company_name` | both | As the filing names itself; the two arms are loaded from different sources, so `Microsoft Corporation` and `MICROSOFT CORP` both occur. Join on `cik`, not on the name |
| `form` | both | `10-K`, `10-Q` |
| `accession_number` | both | The filing |
| `filing_date` | both | When it was submitted |
| `reporting_period_end` | both | When the period ends |
| `source_url` | graph | The filing's EDGAR archive folder. `null` on the structured arm |
| `chunking_version` | RAG's arm | Always `null` from us |
| `extraction_version` | both | How the evidence was made |
| `index_version` | both | What it was retrieved from |
| `metadata` | both | Arm-specific, below. Always carries `trace_id` and `retrieval_method` |

**Exactly one provenance key is populated on every row**: `chunk_id` for RAG, `section_id` for the graph arm, `metadata.fact_id` for the structured arm. Code that assumes `chunk_id` will null-pointer on two of three. The contract enforces this — exactly one, never two.

## `metadata`, by arm

`metadata` is an open mapping on purpose: its keys differ by arm, and pinning them in the published schema would either go stale or drop the ones not listed. Two keys are always there.

| Key | Arm | Meaning |
|-----|-----|---------|
| `trace_id` | both | What you sent, or `null` |
| `retrieval_method` | both | `neo4j_fulltext_claim` or `duckdb_concept_match` |
| `section_title` | both | The heading as the filing writes it; `null` on the structured arm |
| `fact_id` | structured | The provenance key. `sha256(cik, accession, concept, period_start, period_end, period_type, dimensions, unit)` — **the value is deliberately excluded**, so a restatement is comparable to what it restates |
| `concept` | structured | The XBRL tag, e.g. `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` |
| `value_num`, `value_raw` | structured | The number, and the string as filed (`331,839`) |
| `period_start`, `period_end` | structured | The period the fact covers |
| `dimensions` | structured | A **list of records**, `[{axis, member, type}]`, `type` being `explicit` or `typed`. Empty means the consolidated figure; non-empty means a slice of it, and **summing both double-counts** |
| `unit_ref`, `context_ref` | structured | The XBRL unit and context the fact was tagged in |
| `basis` | structured | `as_filed` |
| `corpus_version` | structured | Which build of the fact store answered |
| `claim_id` | graph | The claim's identity: uuid5 of accession, section key and source spans |
| `claim_type` | graph | `stated_fact`, `forward_looking` or `hedged_risk` |
| `subject_scope` | graph | Who the claim is about, when the model named it |
| `section_type`, `section_path` | graph | The shared Item code (`risk_factors`, `mda`), and the heading path to the claim |
| `is_lead_sentence`, `lead_unit_id`, `lead_source` | graph | Whether the sentence leads a risk factor, and which sentence leads it if not |
| `risk_summary` | graph | True inside the risk-factor summary at the top of Item 1A |
| `multi_fragment`, `raw_spans`, `coordinate_system`, `xpath`, `role` | graph | Where the sentence sits in the source, and how to find it again |
| `model` | graph | The model that labelled the claim |
| `hops` | graph | Always `3`: `Claim → Section → Filing → Company`, walked to fill the envelope |
| `extraction_status` | graph | ⚠️ Always `needs_review` for every claim, so it carries no information today (#119) |

## A call and its answer, over the wire

Captured from a real stdio session against the live graph and fact store, unedited apart from one
trimmed `metadata` block. `graphrag_commit` is the commit that served it.

### Handshake

```
protocol : 2025-11-25
server   : kpmg-graphrag 5cf4865aa0f8
tools    : graph_search, structured_search, describe_filters
```

### `structured_search` — what a company reported

Request:

```json
{"query": "Microsoft total revenue",
 "filters": {"ticker": "MSFT", "form": "10-K"},
 "top_k": 1,
 "trace_id": "eval-q-017"}
```

Response:

```json
{
  "request_id": "483b2726-e904-4c66-9ee3-fb66edd81389",
  "status": "answer",
  "reason": null,
  "results": [
    {
      "rank": 1,
      "chunk_id": null,
      "section_id": null,
      "text": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax: 331839000000 USD",
      "quote": null,
      "score": 1.0,
      "section": null,
      "ticker": "MSFT",
      "cik": "0000789019",
      "company_name": "MICROSOFT CORP",
      "form": "10-K",
      "accession_number": "0001193125-26-323660",
      "filing_date": "2026-07-29",
      "reporting_period_end": "2026-06-30",
      "source_url": null,
      "chunking_version": null,
      "extraction_version": "structured-loader-v1",
      "index_version": "duckdb-v1",
      "metadata": {
        "trace_id": "eval-q-017",
        "corpus_version": "edgar-xbrl-v1:20:fae42938",
        "basis": "as_filed",
        "section_title": null,
        "retrieval_method": "duckdb_concept_match",
        "fact_id": "40886d5947d9086e358a90814d45252584b61597f7373baad2a0b53cb5b4d95b",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "context_ref": "C_29985a27-1d12-4b7e-9a06-156523f6e71e",
        "unit_ref": "U_USD",
        "value_raw": "331,839",
        "value_num": 331839000000.0,
        "period_start": "2025-07-01",
        "period_end": "2026-06-30",
        "dimensions": []
      }
    }
  ],
  "count": 1,
  "arm": "structured",
  "graphrag_commit": "5cf4865aa0f8dc815ff8e69255e20d60adbe7c81"
}
```

### `graph_search` — what a company said

Request:

```json
{"query": "cybersecurity incidents",
 "filters": {"ticker": "MSFT", "fiscal_year": 2026},
 "top_k": 1,
 "trace_id": "eval-q-018"}
```

Response, with eleven `metadata` keys elided for length:

```json
{
  "request_id": "386b5b52-6705-4efd-81ac-10aa0753a0c3",
  "status": "answer",
  "reason": null,
  "results": [
    {
      "rank": 1,
      "chunk_id": null,
      "section_id": "4131905c-59aa-409e-8694-9b2aeab1dbd7",
      "text": "If any of these providers fail to meet our requirements, or experience service interruptions, operational failures, capacity constraints, physical damage, cybersecurity incidents, or other disruptions, our ability to provide services, maintain system performance, or meet customer expectations could be negatively impacted.",
      "quote": "If any of these providers fail to meet our requirements, or experience service interruptions, operational failures, capacity constraints, physical damage, cybersecurity incidents, or other disruptions, our ability to provide services, maintain system performance, or meet customer expectations could be negatively impacted.",
      "score": 3.981825590133667,
      "section": "part-i-item-1a",
      "ticker": "MSFT",
      "cik": "0000789019",
      "company_name": "Microsoft Corporation",
      "form": "10-K",
      "accession_number": "0001193125-26-323660",
      "filing_date": "2026-07-29",
      "reporting_period_end": "2026-06-30",
      "source_url": "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/",
      "chunking_version": null,
      "extraction_version": "claim-pipeline-v1",
      "index_version": "neo4j-fulltext-v1",
      "metadata": {
        "trace_id": "eval-q-018",
        "retrieval_method": "neo4j_fulltext_claim",
        "claim_id": "2491cf28-e6d8-5e47-8e5c-4e38198ee904",
        "claim_type": "hedged_risk",
        "section_type": "risk_factors",
        "section_path": "Part I Item 1A > OPERATIONAL RISKS",
        "is_lead_sentence": false,
        "hops": 3,
        "extraction_status": "needs_review",
        "\u2026": "11 more: xpath, raw_spans, coordinate_system, role, model, lead_unit_id, lead_source, risk_summary, multi_fragment, subject_scope, section_title"
      }
    }
  ],
  "count": 1,
  "arm": "graph",
  "graphrag_commit": "5cf4865aa0f8dc815ff8e69255e20d60adbe7c81"
}
```

Note the two scores: **1.0** from the structured arm and **3.98** from the graph arm, for equally
good matches. They are raw numbers from two different engines. Use the order, never the value.

### Nothing found

```json
{"query": "zzzqqq nonexistent phrase xyzzy", "filters": {"ticker": "MSFT"}, "top_k": 3}
```

```json
{
  "request_id": "d4ebe7f3-4e6c-45ba-b139-d4f3b7ffa5f6",
  "status": "abstain",
  "reason": "missing_fact",
  "results": [],
  "count": 0,
  "arm": "graph",
  "graphrag_commit": "5cf4865aa0f8dc815ff8e69255e20d60adbe7c81"
}
```

The arm looked and the corpus does not hold it. This is an answer about the world, not a failure.

### A refused filter

```json
{"query": "x", "filters": {"max_hops": 2}}
```

```
is_error: true
Error executing tool graph_search: the graph arm cannot filter on max_hops: this arm walks exactly 3 hops, Claim to Section to Filing to Company, and they run upward to fill the evidence envelope rather than outward to find anything. Fewer cannot name the filing a claim came from; more has nowhere to go. Depth is structural here, not a setting. A traversal whose depth can vary is neighbours(), which is not built yet
```

This is a failure, not an answer about the world. Do not record it as "no evidence found".

## The published schemas

Read them off the server with any MCP client — `list_tools` carries both. Reproduced here so you
can read them without running anything.

### Input, `graph_search` (`structured_search` is identical)

```json
{
  "properties": {
    "query": {
      "title": "Query",
      "type": "string"
    },
    "filters": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Filters"
    },
    "top_k": {
      "default": 10,
      "title": "Top K",
      "type": "integer"
    },
    "trace_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Trace Id"
    }
  },
  "required": [
    "query"
  ],
  "type": "object",
  "title": "graph_searchArguments"
}
```

### Output, top level

```json
{
  "properties": {
    "request_id": {
      "title": "Request Id",
      "type": "string"
    },
    "status": {
      "title": "Status",
      "type": "string"
    },
    "reason": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reason"
    },
    "results": {
      "items": {
        "$ref": "#/$defs/Evidence"
      },
      "title": "Results",
      "type": "array"
    },
    "count": {
      "title": "Count",
      "type": "integer"
    },
    "arm": {
      "title": "Arm",
      "type": "string"
    },
    "graphrag_commit": {
      "title": "Graphrag Commit",
      "type": "string"
    }
  },
  "required": [
    "request_id",
    "status",
    "reason",
    "results",
    "count",
    "arm",
    "graphrag_commit"
  ],
  "type": "object",
  "description": "What a search tool returns: the decision, the envelope, and the stamp.\n\n`status` is `answer`, `abstain` or `clarify`, and `reason` says which kind\nwhen it is not an answer \u2014 `missing_fact`, `out_of_scope`, `false_premise`,\n`fabricated_relationship`, or `ambiguous` for a clarify. An answer always\ncarries evidence and never a reason; everything else carries a reason and\nno evidence.\n\nEmpty `results` on its own does not say whether the arm looked and found\nnothing or declined to look. The decision does, and an agent deciding\nwhether to answer needs it more than it needs the rows.",
  "title": "SearchResponse"
}
```

### Output, one evidence item

```json
{
  "description": "One item of the contract envelope, exactly as an arm returns it.\n\nDeclared so the endpoint publishes a real output schema. A tool that\nreturns a bare dict publishes `{\"type\": \"object\"}`, which tells a consumer\nnothing \u2014 and the first thing the Agent team asked for was the contract.\n\n`contract.EVIDENCE_FIELDS` is the source of truth and a test holds this to\nit field for field, so the published schema cannot drift from the envelope\nit describes.\n\n`metadata` stays a free-form mapping on purpose: its keys differ by arm\n(`fact_id`, `concept` and `dimensions` on the structured arm, `hops` and\n`section_title` on the graph arm), and pinning them here would either go\nstale or quietly drop the ones not listed.",
  "properties": {
    "rank": {
      "title": "Rank",
      "type": "integer"
    },
    "chunk_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chunk Id"
    },
    "section_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Section Id"
    },
    "text": {
      "title": "Text",
      "type": "string"
    },
    "quote": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Quote"
    },
    "score": {
      "title": "Score",
      "type": "number"
    },
    "section": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Section"
    },
    "ticker": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ticker"
    },
    "cik": {
      "title": "Cik",
      "type": "string"
    },
    "company_name": {
      "title": "Company Name",
      "type": "string"
    },
    "form": {
      "title": "Form",
      "type": "string"
    },
    "accession_number": {
      "title": "Accession Number",
      "type": "string"
    },
    "filing_date": {
      "title": "Filing Date",
      "type": "string"
    },
    "reporting_period_end": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Reporting Period End"
    },
    "source_url": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Source Url"
    },
    "chunking_version": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Chunking Version"
    },
    "extraction_version": {
      "title": "Extraction Version",
      "type": "string"
    },
    "index_version": {
      "title": "Index Version",
      "type": "string"
    },
    "metadata": {
      "additionalProperties": true,
      "title": "Metadata",
      "type": "object"
    }
  },
  "required": [
    "rank",
    "chunk_id",
    "section_id",
    "text",
    "quote",
    "score",
    "section",
    "ticker",
    "cik",
    "company_name",
    "form",
    "accession_number",
    "filing_date",
    "reporting_period_end",
    "source_url",
    "chunking_version",
    "extraction_version",
    "index_version",
    "metadata"
  ],
  "title": "Evidence",
  "type": "object"
}
```

## Errors

**An empty `results` means the arm looked and found nothing. Anything else raises**, and the message says why.

| What | You get |
|------|---------|
| A filter the arm refuses | The reason and, where one exists, what to use instead |
| A filter key that is not in the contract | `unsupported filter: <key>`, with the right name if it is a known rename |
| `top_k` outside 1–50, or an empty query | What the limit is |
| An arm that is not configured | Which arm, and that it is not configured |
| An arm that is down | That it is down, and that this is an outage rather than an absence of evidence. What the driver said stays in the server log — the error names the host, and on Aura the instance id in the host is also the username |

Do not treat an error as "no evidence". An agent that abstains on an outage abstains confidently and wrongly, and nothing in the transcript says why.

## Versions on every answer

Three versions travel with every call, because three teams read the same numbers at different times.

| Where | What it pins |
|-------|--------------|
| `graphrag_commit` on the response | The code that served it. Eval track `main` but lag it — round 1 ran four PRs behind — so quote this beside any number you report |
| `metadata.corpus_version` (structured) | Which build of the fact store answered |
| `extraction_version`, `index_version` | How the evidence was made, and what it was retrieved from |
