# Pilot filing manifest

Prepared by CW, 2026-10-16.
Related to #5 and PR #10.

Status: Three starting filings verified against SEC filing-detail pages.
These are initial pilot candidates, pending alignment with the team's evaluation corpus.

## Verified starting filings

| Ticker | Company | CIK | Form | Reporting period end | Filing date | Accession number |
| --- | --- | --- | --- | --- | --- | --- |
| MSFT | Microsoft Corporation | 0000789019 | 10-K | 2025-06-30 | 2025-07-30 | 0000950170-25-100235 |
| JPM | JPMorgan Chase & Co. | 0000019617 | 10-K | 2025-12-31 | 2026-02-13 | 0001628280-26-008131 |
| AAPL | Apple Inc. | 0000320193 | 10-K | 2025-09-27 | 2025-10-31 | 0000320193-25-000079 |

## Official source links

### MSFT

- Filing detail: https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/0000950170-25-100235-index.html
- Primary document: https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/msft-20250630.htm
- The filing index lists Exhibit 21 and an extracted XBRL instance.

### JPM

- Filing detail: https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/0001628280-26-008131-index.htm
- Primary document: https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm
- The filing index lists Exhibit 21 and an extracted XBRL instance.

### AAPL

- Filing detail: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.html
- Primary document: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm
- The filing index lists Exhibit 21.1 and an extracted XBRL instance.
- This is the source used for the six annotated Claim examples.

## Verification limits and remaining work

- Filing metadata and attachment listings have been checked.
- Exhibit contents, section structure and extraction readiness remain to be validated.
- The three fiscal years have different reporting-period end dates.
- These filings have not yet been automatically extracted or loaded as a real-filing graph.
- A fourth company with a recent original 10-K and corresponding 10-K/A remains to be selected.
- The amendment pair must support a documented financial correction with relevant tagged facts; a 10-K/A form alone does not establish restatement.
