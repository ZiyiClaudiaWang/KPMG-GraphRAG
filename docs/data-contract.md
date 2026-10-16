# Data contract — Data → GraphRAG

> **Revised 2026-10-13, after reading your repo.** Most of what earlier versions asked for is already built. Those asks are withdrawn below rather than quietly dropped, so you can see what we are no longer waiting on. Four things remain.
>
> This supersedes the Notion page, which is frozen — that workspace has used all its free blocks.

## What we are withdrawing

We read `michaelliruoxi/data_engineering_kpmg@main` from the migrations. These were in our earlier contract and you have already built them, in several cases better than we specced:

| We asked for | You already have |
|---|---|
| Sections as first-class records — our stated biggest ask | `sec.report_sections`, with `part`, `item`, `section_order`, `offset_start`, `offset_end`, `original_title`, `source_locator`, and a 25-code `section_type` vocabulary |
| A provenance shape for table cells | `sec.table_cells` — `row_index`, `column_index`, spans, `kind`, `row_heading`, `column_headings`, `column_period`, `is_total`, and a required XPath |
| Inline-XBRL linkage from numbers back to position | `sec.cell_fact_links`, with `link_method` of `inline_id` or `same_row_semantic_repeat` |
| Keep XBRL dimensions | `financial_facts.dimensions`, kept as an array |
| Keep the taxonomy namespace rather than a friendly label | `concept_namespace` and `concept_name`, stored separately |
| The raw string beside the parsed number | `raw_value` and `numeric_value`, plus `is_nil` |
| Filer-asserted precision | `reported_decimals` and `reported_precision` |
| Amendments linked rather than reconciled | `filings.amendment_of_id` |
| Content checksums | `source_documents.sha256` |
| Exhibits as separate documents | `source_documents`, one-to-many per filing, with `role` and `document_type` |
| An `extraction_version` concept | Already on `reports` and `financial_facts`, under that exact name |
| "Is the normalized document retrievable?" | `reports.full_text` — yes |

`is_total` on table cells is a better catch than ours. We only flagged total-versus-part for XBRL dimensions; you solved it for tables too.

Your field names — `accession_number`, `form`, `filing_date`, `reporting_period_end`, 10-digit `cik` — already match the RAG team's retrieval contract. We renamed ours to match last week. **Data and RAG had converged and we were the outlier**, which is worth saying out loud because it means the vocabulary question is settled.

## We also had XPath backwards

Earlier versions of this contract insisted character offsets into normalized text were the coordinate system, and treated XPath as a complication. That was wrong.

`table_cells.source_locator` requires an `xpath`. `report_sections.source_locator` is the same shape. Eval's gold set locates evidence by XPath. RAG reserves `source_locator` as a metadata key for exactly this. **Three teams use XPath into the source HTML. We were the only one insisting on something else.**

Revised position, and we will carry it in our own docs:

- **XPath is the cross-team locator.** It is what Eval scores against and what identifies a region across teams
- **Normalized offsets stay for extraction and highlighting**, where a character range into retrievable text is what the work actually needs
- **The verbatim quote is what UI matches on**, so rendering never depends on either coordinate system staying in sync
- When XPath and offsets disagree, **XPath wins** and the offsets get rebuilt

You already store both, so this is a documentation decision rather than engineering. We would like it written down somewhere all four teams can point at.

---

## What we still need

Four things, smallest first.

### 1 · A stable identifier we can anchor on

Our graph does not copy your text. It stores pointers back to it — an edge records that a relationship came from a particular section. If that identifier changes value, every citation we hold breaks at once, and it breaks silently: a dangling UUID does not raise, it just stops resolving.

Re-ingest is safe. `store_report_sections` upserts `ON CONFLICT (report_id, section_key) DO NOTHING`, so the same filing fed in twice keeps its row and its UUID.

A **rebuild into a fresh database** is not. New environment, migration reset, a dev/prod split, someone re-running from scratch — `gen_random_uuid()` mints new values for identical content and our provenance goes stale all at once.

The stable identifiers already exist underneath: `accession_number`, `section_key`, `cell_key`, `occurrence_key`. The UUID is the surface. So, either:

- **Derive the UUID rather than randomising it** — `uuid5(namespace, accession_number + section_key)`. Still a UUID, every foreign key unchanged, no migration, survives a rebuild. This is our preference
- **Or expose the natural key** alongside it, so we anchor on `(accession_number, section_key)` and never hold a UUID

And one question that decides how much this matters: **is `section_key` derived from content, or from position in the parse?** If positional, inserting a section upstream shifts every key after it and neither option helps.

### 2 · `basis` on facts

`financial_facts` has no `as_filed` / `as_restated` field. `filings.amendment_of_id` makes it derivable, but nobody has asserted it.

This is our most load-bearing gap. Without it, "what was R&D spend in FY2023" has two correct answers that disagree, and nothing in the data says which is which. Every numeric answer we return has to name its basis and cite an accession.

**We need two things, and the second is the one usually left out.**

**One — the label.** Either assert `basis` on the fact, or confirm we derive it from `amendment_of_id` and we will build that. We only need to know which. The values are `as_filed` and `as_restated`; we have settled that spelling our end so the field has one name wherever it appears.

**Two — what identifies which fact replaced which.** `amendment_of_id` is filing-level, and a correction is not. Asking "was this filing amended" marks every number in the original out of date, including periods the amendment never touched — we built a graph on 14 Oct that does exactly this, and it left a year-on-year comparison with no baseline. Concept plus period is the minimum; an explicit pointer from the replacement to what it replaced is better.

A number is out of date only when the amendment replaces **that same concept for that same period**. The label alone cannot tell us that.

Note that `basis` says where a number came from, not whether it is current. Whether it is current is computed from the amendment relation at query time. A fact can be `as_filed` and perfectly current — most of them are.

### 3 · Note-level codes in `section_types`

All 25 codes are Item-level. A 10-Q puts segment data in Note 16, which sits inside `financial_statements` — so the whole of Item 1, statements and twenty-odd notes together, carries one label.

Segment data and the relationships around it are among the most valuable content in the filing for us, and at Item granularity we cannot filter to them or cite them precisely.

Two parts, and we should be honest that the second is the real one. Adding a code — something like `financial_note` — is an INSERT into a lookup table, with the note's own number and title living in `original_title` and `section_order`. But the vocabulary is the cheap half: the parser also has to detect note boundaries and emit a row per note. Inline XBRL marks those boundaries with `ix:continuation` and `ix:nonnumeric`, so it is structural rather than heading heuristics, but it is still work. `section_order` and `offset_start` already provide the ordering a split would need. Reasoning and the open decisions in [section-vocabulary.md](./section-vocabulary.md) — this one needs RAG and Eval in the room too, since all three of us label sections differently.

### 4 · Five metadata fields

Free at parse time, annoying to backfill:

- `sic`, `state_of_incorp`, `exchange` on `companies`
- `fiscal_year`, `fiscal_period` on `filings` — RAG filters on both

## Questions

- **Read-only database credentials**, so this stops being archaeology from migration files
- When do chunks land on `main`? `chunking.py` and `section_id_for_chunk` exist, but no chunk table is merged
- Is `reports.sections` jsonb or `sec.report_sections` authoritative, and what keeps them from drifting?
- Are exhibits parsed in practice? `source_documents.role` and `document_type` allow it — is Ex-21 actually among them?
- Which tickers and which years are in the pilot corpus? Eval has two gold sets implying two different corpora
- Are DEF 14A or Form 4 in scope? If not we will drop officers from our schema rather than carry a half-empty node
- One request: **`gold_set_v0.md` must never be indexed.** Your repo already holds the same MSFT filing, so the leakage risk is live

## Still true from the earlier version

Four things not to do, all of which your schema already respects — recorded so they survive a refactor:

1. **Don't reconcile amendments.** Pass through both the original and the amended fact. Deciding which wins is our modelling problem
2. **Don't rename concepts** to readable labels
3. **Don't drop dimensional facts** to tidy a table. A total and a part share a concept and a period and differ only by dimension; summing both double-counts, and the answer looks plausible
4. **Don't renumber IDs** on a re-parse of unchanged content — see item 1 above
