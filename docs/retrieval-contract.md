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
| `section` | applied | **ignored** | `Part I Item 1A`, matched as a whole-word prefix. The fact table has no section pointer (#40) |
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
| `section` | graph | `Part I Item 1A`, with the Part prefix. `null` on the structured arm |
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
