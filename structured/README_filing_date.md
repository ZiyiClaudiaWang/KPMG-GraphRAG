# Rebuilding the fact database

The `.duckdb` file is not in the repository. Rebuild it from the filing:

```bash
export SEC_USER_AGENT="Your Name your.email@school.edu"   # SEC asks for a contact

curl -H "User-Agent: $SEC_USER_AGENT" \
  https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/msft-20250630.htm \
  -o msft-10k.htm

python3 structured/fact_loader_v1.py \
  --html msft-10k.htm \
  --cik 0000789019 --ticker MSFT \
  --accession 0000950170-25-100235 \
  --output msft_financial_facts_v1.duckdb
```

Expect 1,551 raw facts, 1,429 canonical, 2 held for review.

`--filing-date` is optional. Without it the loader asks EDGAR when the accession
was filed, so the date comes from the source rather than from whoever ran the
command. Pass it explicitly when working offline.

## Using it through the retrieval contract

```python
from graphrag import retrieve
from structured_query_v1 import structured_search

retrieve.configure_structured_backend(structured_search)

rows = retrieve.structured_search(
    "Intelligent Cloud revenue",
    filters={"segment": "Intelligent Cloud",
             "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"},
    top_k=3, trace_id="demo-1")
```

Set `MSFT_FACT_DB_PATH` if the database is not in the working directory.

## Still hardcoded in the envelope

`company_name` and `form` are literals, because `financial_facts_v1` has no
column for either. They are filing-level constants and want the same treatment
`filing_date` just had: a column written at load time. Until then a second
filing will report the first filing's company and form.
