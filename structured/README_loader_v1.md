# Financial Facts Loader v1

## Overview
Extracts inline XBRL financial facts from SEC 10-K HTML filings
and stores them in DuckDB.

Initial validated filing: Microsoft FY2025 10-K.

## Requirements
Python 3
pip install pandas beautifulsoup4 lxml duckdb

## Usage
python fact_loader_v1.py \
  --html msft_2025_10k.htm \
  --cik 0000789019 \
  --ticker MSFT \
  --accession 0000950170-25-100235 \
  --output msft_financial_facts_v1.duckdb

## Output Tables
- facts_raw_v1: All extracted XBRL fact occurrences.
- financial_facts_v1: Deduplicated canonical financial facts.
- facts_review_v1: Conflicting facts requiring review.

## Dimensions
Supports explicit and typed XBRL dimensions.
Dimensions are stored as JSON.

## Fact IDs
Deterministic SHA-256 IDs based on filing identity,
concept, period, dimensions, and unit.

## Validation Results
Microsoft FY2025 10-K:
- Raw facts: 1,551
- Canonical facts: 1,429
- Review rows: 2
- Unique canonical fact IDs: 1,429

End-to-end, command-line, and reproducibility tests passed.

## Limitations
- Validated on Microsoft FY2025 10-K only.
- Conflicting facts are excluded from canonical output
  and retained for manual review.
- Fact ID and schema conventions require final team alignment.

## Structured Query v1

`structured_query_v1.py` provides `structured_search(query, filters=None, top_k=10, trace_id=None)` over the Microsoft FY2025 10-K DuckDB fact table. Set `MSFT_FACT_DB_PATH` to the database file path before querying.

Supported filters: `ticker`, `cik`, `accession_number`, `concept`, `reporting_period_end`, `segment`, and `basis` (`as_filed` only). Results use the Mock retrieval Evidence Envelope structure and include `fact_id`, numeric value, unit reference, periods, and dimensions.

## Quantitative Gold Questions

`msft_gold_questions_v1.json` contains 10 Microsoft FY2025 financial questions with expected answers, units, concepts, periods, and fact IDs.

## Known Limitations

- Only Microsoft FY2025 10-K facts are loaded; real 10-K/A restatements are not available.
- `as_restated` is unsupported.
- Some Evidence Envelope fields are placeholders (`None`).
- Not all Mock filters are implemented; `compare_periods` is not yet implemented.
- Fact IDs are provisional and may require alignment with repository ID conventions.
