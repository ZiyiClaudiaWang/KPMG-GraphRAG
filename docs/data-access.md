# Connecting to the Data Team's Database from Python

Oct 25, 2026 · @Kelly

GraphRAG reads the Data team's SEC filings straight from their PostgreSQL database on AWS, with our read-only `graphrag` login. Setup takes about ten minutes; after that, one command exports section text for any loaded company. On Oct 25 the database held 448 filings from 280 companies, and the backfill is still running.

| Setting | Value |
| --- | --- |
| Host | `sec-filings.cghyw6082mug.us-east-1.rds.amazonaws.com` |
| Port / database | `5432` / `sec_filings` |
| Username | `graphrag` |
| Password | Project Hub, section 2.2. Never put it in code, notebooks or Slack |

You need Python 3.11+ (or uv), a network that allows port 5432, and the script [`tools/graphrag_data.py`](../tools/graphrag_data.py) from this repo.

## 1. Set up (once)

Pick uv or pip:

```bash
# Option A: uv
mkdir db-test && cd db-test          # any folder outside a Git repo
uv init --python 3.13
uv add "psycopg[binary]" pandas python-dotenv
```

```bash
# Option B: pip
mkdir db-test && cd db-test          # any folder outside a Git repo
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install "psycopg[binary]" pandas python-dotenv
```

Then, either way, in the same folder:

```bash
# AWS certificate. The second command must print -----BEGIN CERTIFICATE-----
curl -o us-east-1-bundle.pem https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem
head -1 us-east-1-bundle.pem

# Keep the password, certificate and exports out of Git
printf '.env.aws\n*.pem\ndata/\n' >> .gitignore

# Full path of the certificate, for the last line of .env.aws
echo "$(pwd)/us-east-1-bundle.pem"
```

Create a plain-text file named `.env.aws` in the folder with these lines. Copy `tools/graphrag_data.py` there too.

```bash
# No quotes, no spaces around "=".
PGHOST=sec-filings.cghyw6082mug.us-east-1.rds.amazonaws.com
PGPORT=5432
PGDATABASE=sec_filings
PGUSER=graphrag
# Password from the Project Hub. The Hub shows "\_"; type only "_".
PGPASSWORD=PASTE_HERE
PGSSLMODE=verify-full
# Paste the path printed by the echo command above.
PGSSLROOTCERT=/full/path/to/db-test/us-east-1-bundle.pem
```

## 2. Check the connection

```bash
# uv: uv run graphrag_data.py ...    pip: python graphrag_data.py ...
uv run graphrag_data.py filings --tickers AAPL
```

A table of Apple filings means everything works:

```bash
ticker form  fiscal_year fiscal_period reporting_period_end filing_date     accession_number  n_sections
  AAPL 10-Q         2026            Q3           2026-06-27  2026-07-31 0000320193-26-000020          14
  AAPL 10-Q         2026            Q2           2026-03-28  2026-05-01 0000320193-26-000013          14
  AAPL 10-K         2025            FY           2025-09-27  2025-10-31 0000320193-25-000079          28
```

## 3. Export sections

```bash
# Which sections were cut, without text (fast)
uv run graphrag_data.py inventory --tickers AAPL MSFT JPM --form 10-K --latest

# Export the text: data/sections/<TICKER>_<accession>.jsonl (one line per section)
# plus data/manifest_<time>.csv (which filings and versions were exported)
uv run graphrag_data.py fetch --tickers AAPL MSFT JPM --form 10-K --latest --section-types risk_factors

# Filters, same on every command:
#   --tickers AAPL MSFT                 companies
#   --accessions 0000320193-25-000079   exact filings, for reproducible runs
#   --form 10-K | 10-Q                  leave out for both
#   --since / --until YYYY-MM-DD        filing-date range
#   --latest                            each company's newest filing only
#   --section-types risk_factors mda business market_risk financial_statements
```

Or from a notebook or the pipeline:

```python
from graphrag_data import connect, find_filings, fetch_sections

with connect() as conn:                                   # read-only connection
    filings = find_filings(conn, tickers=["AAPL"], form="10-K")
    rows = fetch_sections(conn, filings.filing_id[0], section_types=["risk_factors"])

# Each row is one section: rows[0]["content"] is its text (paragraphs split on "\n\n"),
# the other fields are provenance (accession_number, section_key, source_locator, ...).
```

## Store with every extraction

Anchor each claim on `accession_number` + `section_key`, not on the UUIDs, which change if the database is rebuilt. Also log `processing_set_id` (the version you read) and keep `source_locator` (XPath, the locator shared with RAG and Eval). `fiscal_year` and `fiscal_period` are computed by our script, not supplied by the Data team.

## If something fails

| Error | Fix |
| --- | --- |
| `ModuleNotFoundError` | Install the packages in this folder's environment; run with `uv run` or with the venv activated |
| `password authentication failed` | Re-copy the password: no `\` before `_`, no trailing space |
| Certificate or SSL error | `PGSSLROOTCERT` must be the full path to a `.pem` file |
| Hangs, then times out | The network blocks port 5432 (common on campus Wi-Fi). Try a hotspot, then ask Michael |
| `No matching filings are loaded yet.` | Drop `--form`, check the ticker; the company may not be loaded yet |

## References

- [Data team repo and README](https://github.com/michaelliruoxi/data_engineering_kpmg)
- [Financial browsing schema](https://github.com/michaelliruoxi/data_engineering_kpmg/blob/main/docs/database-finance.md) and [diagram](https://github.com/michaelliruoxi/data_engineering_kpmg/blob/main/docs/database-finance.png)
- [GraphRAG data contract](data-contract.md)
