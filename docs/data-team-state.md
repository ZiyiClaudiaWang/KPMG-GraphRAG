# What Data has already built

Read from `michaelliruoxi/data_engineering_kpmg@main` on 2026-10-13, from the migrations rather than the docs. Five people are working in parallel on branches; a lot is already merged.

**Headline: most of our data contract already exists, and parts of it are better than what we asked for.** Several asks in earlier versions of our contract should be withdrawn rather than sent.

## Already built — withdraw these asks

| We were going to ask for | They already have |
|---|---|
| **Sections as first-class records** — our "single biggest ask" | `sec.report_sections`, with `part`, `item`, `section_order`, `offset_start`, `offset_end`, `original_title`, `source_locator`, and a 25-code `section_type` vocabulary |
| A provenance shape for table cells | `sec.table_cells` — `row_index`, `column_index`, spans, `kind`, `row_heading`, `column_headings`, `column_period`, `is_total`, and a **required** XPath in `source_locator` |
| Inline-XBRL linkage from numbers to position | `sec.cell_fact_links`, with `link_method` of `inline_id` or `same_row_semantic_repeat`, plus an `evidence` payload |
| **Keep XBRL dimensions** | `financial_facts.dimensions` jsonb, kept as an array |
| Keep the taxonomy namespace, not a friendly label | `concept_namespace` and `concept_name` stored separately |
| Raw string beside the parsed number | `raw_value` and `numeric_value`, plus `is_nil` |
| Filer-asserted precision | `reported_decimals` and `reported_precision` |
| Amendments linked, not reconciled | `filings.amendment_of_id`, a self-referencing FK |
| Is the normalized document retrievable? | `reports.full_text` — yes |
| Content checksums | `source_documents.sha256`, required and format-checked |
| Exhibits as separate documents | `source_documents` is one-to-many per filing, with `role` and `document_type` |
| An `extraction_version` concept | Already on `reports` and `financial_facts`, under that exact name |

`is_total` on table cells is a nice catch on their side — it is the table-level version of the total-versus-part problem we flagged for XBRL dimensions.

## Their naming already matches RAG's

`accession_number`, `form`, `filing_date`, `reporting_period_end`, `source_url`, `cik` as a 10-digit checked string. These are the names we adopted from RAG's contract last week, which means **Data and RAG already agreed a vocabulary and we were the outlier.** Our rename was the right call and is now confirmed from two directions.

## What is genuinely still open

### 1 · Identity — the real question, and not the one we were going to ask

Every primary key is `gen_random_uuid()`. Our contract says "never a random UUID", which reads as a direct conflict — but it isn't, quite. The stable identifiers are the **natural keys** underneath:

| Table | Natural key |
|---|---|
| `report_sections` | `(report_id, section_key)` |
| `table_cells` | `(table_id, cell_key)` |
| `financial_facts` | `(source_document_id, extraction_version, occurrence_key)` |
| `filings` | `accession_number` |

`store_report_sections` upserts with `ON CONFLICT (report_id, section_key) DO NOTHING`, so re-ingesting the same filing preserves existing rows and existing UUIDs.

**So the ask changes.** Not "make your IDs deterministic" but:

- Is `section_key` derived from content, or from position in the parse? If positional, inserting one section upstream shifts every key after it
- Do UUIDs survive a **rebuild into a fresh database**, or only a re-ingest into the existing one? Our graph cannot anchor on an ID that changes when the database is recreated
- Can the natural key be exposed alongside the UUID, so we anchor on `(report_id, section_key)` and never hold a UUID at all?

That third option costs them nothing and removes the risk entirely. It is the thing to ask for.

### 2 · XPath is the shared locator, and we were the odd ones out

`table_cells.source_locator` has a **required** `xpath`. `report_sections.source_locator` is the same shape. Eval's gold set locates evidence by XPath. RAG reserves `source_locator` as a metadata key for exactly this.

Three teams use XPath into the source HTML. We insisted on character offsets into normalized text, and called XPath "a third coordinate system in the scorer". That was backwards — **XPath is the common one and our normalized offsets are the outlier.**

Revised position: carry both. XPath for cross-team identity and for Eval, normalized offsets for extraction and highlighting, and the `quote` as the thing UI matches on. Say explicitly which is authoritative when they disagree. Data already stores both, so this is a documentation decision rather than engineering.

### 3 · Still missing, and still worth asking for

- **`basis` on facts.** Confirmed absent from `financial_facts`. `amendment_of_id` makes as-filed versus as-restated derivable, but nobody has asserted it. Still our most load-bearing gap
- **`sic`, `state_of_incorp`, `exchange`** on `companies` — absent
- **`fiscal_year`, `fiscal_period`** on `filings` — absent, and RAG filters on both
- **Chunks are not on `main`.** `chunking.py` exists and `sections.py` has `section_id_for_chunk`, so chunks are designed to hang off sections — which is exactly the shape we asked for — but no chunk table is merged. Likely on `ruby/chunking-citations`
- **Two representations of sections.** `reports.sections` is a jsonb column and `sec.report_sections` is a table. Which is authoritative, and what keeps them from drifting?

### 4 · The section-vocabulary question survives, in a new form

`section_types` is a 25-code controlled vocabulary — `business`, `risk_factors`, `mda`, `financial_statements`, `exhibits` and so on. All of them are **Item-level**. There is no note-level code, so the notes inside `financial_statements` appear not to get their own section rows.

That is the same granularity problem, and it now has three shapes rather than two:

| Team | Section model |
|---|---|
| Data | `part` + `item` + `section_type` from a 25-code vocabulary + `original_title` |
| RAG | `section` as `"Part I Item 1"` + `metadata.section_title` |
| GraphRAG | `section_path` to the leaf |

Data's and RAG's are mechanically reconcilable — `section` is `part` plus `item`. Nobody has written that mapping down, and neither reaches a note.

**Better ask than before:** `section_types` is a table, so adding note-level codes is an INSERT, not a schema change. And `section_order` plus `offset_start` already gives the ordering a sub-section split would need.

## What to do with this

1. Cut the withdrawn asks from the contract before re-sending. Sending a team a list of things they finished a week ago costs credibility we will need for the asks that remain
2. Lead with the identity question, which is small, concrete and genuinely unresolved
3. Reverse our position on XPath in writing
4. Get read access to the database so this stops being archaeology from migrations
