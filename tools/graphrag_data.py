"""
GraphRAG data access: find filings, inspect their sections, and batch-export section text.

Read-only. Reads the Data team's `finance` views with the settings in .env.aws,
so run it from the folder that holds .env.aws.
Setup: see the doc "Connecting to the Data Team's Database from Python".

Run with uv:   uv run graphrag_data.py <command> [options]
Run with pip:  python graphrag_data.py <command> [options]   (with your venv activated)

Commands
    filings    list matching filings: form, fiscal year/period, accession, section count
    inventory  list their sections without text (fast; check before exporting)
    fetch      export section text: data/sections/<TICKER>_<accession>.jsonl
               (one line per section) plus data/manifest_<UTC time>.csv

Options (all commands; give --tickers or --accessions)
    --tickers AAPL MSFT JPM          companies, any case
    --accessions 0000320193-25-000079  exact filings; use these for reproducible runs
    --form 10-K | 10-Q               leave out for both
    --since / --until YYYY-MM-DD     filing-date range
    --latest                         each company's newest matching filing only
    --section-types risk_factors mda business market_risk financial_statements

Examples
    uv run graphrag_data.py filings --tickers AAPL
    uv run graphrag_data.py inventory --tickers AAPL MSFT JPM --form 10-K --latest
    uv run graphrag_data.py fetch --tickers AAPL MSFT JPM --form 10-K --latest --section-types risk_factors

Notes
    - Each exported record carries `content` (the section text; paragraphs are separated
      by a blank line, "\\n\\n") and provenance fields. Anchor extractions on
      accession_number + section_key, not on the UUIDs, which change if the database is rebuilt.
    - fiscal_year / fiscal_period are derived here, not supplied by the Data team;
      unusual fiscal calendars can come out one year off.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(".env.aws")

OUT_DIR = Path("data")


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #

FILINGS_SQL = """
    SELECT f.filing_id, f.ticker, f.cik, f.company_name, f.form,
           f.accession_number, f.filing_date, f.reporting_period_end,
           f.processing_set_id, f.set_key, f.selection_reason, f.validation_status,
           (SELECT count(*) FROM finance.filing_section s
             WHERE s.filing_id = f.filing_id
               AND s.processing_set_id = f.processing_set_id) AS n_sections,
           -- fiscal-year-end month, taken from the company's latest 10-K
           -- (minus 10 days so a 52/53-week year ending Oct 1 or Jan 2 counts as Sep / Dec)
           (SELECT extract(month FROM k.reporting_period_end - 10)::int
              FROM finance.filing k
             WHERE k.company_id = f.company_id AND k.form = '10-K'
               AND k.reporting_period_end IS NOT NULL
             ORDER BY k.filing_date DESC LIMIT 1) AS fye_month
    FROM finance.filing f
    WHERE (%(tickers)s::text[]    IS NULL OR f.ticker = ANY(%(tickers)s::text[]))
      AND (%(accessions)s::text[] IS NULL OR f.accession_number = ANY(%(accessions)s::text[]))
      AND (%(form)s::text         IS NULL OR f.form = %(form)s::text)
      AND (%(since)s::date        IS NULL OR f.filing_date >= %(since)s::date)
      AND (%(until)s::date        IS NULL OR f.filing_date <= %(until)s::date)
    ORDER BY f.ticker, f.filing_date DESC
"""

# Section metadata only: cheap, used to check coverage before pulling text.
INVENTORY_SQL = """
    SELECT f.ticker, f.form, f.accession_number, f.filing_date,
           s.section_id, s.section_key, s.section_order, s.part, s.item,
           s.section_type, s.original_title, s.section_validation_status,
           s.offset_end - s.offset_start AS n_chars
    FROM finance.filing f
    JOIN finance.filing_section s
      ON s.filing_id = f.filing_id
     AND s.processing_set_id = f.processing_set_id
    WHERE f.filing_id = ANY(%(filing_ids)s::uuid[])
      AND (%(section_types)s::text[] IS NULL OR s.section_type = ANY(%(section_types)s::text[]))
    ORDER BY f.ticker, f.filing_date DESC, s.section_order
"""

# Full section records, including text, for one filing at a time.
SECTIONS_SQL = """
    SELECT f.accession_number, f.ticker, f.cik, f.company_name, f.form,
           f.filing_date, f.reporting_period_end,
           f.processing_set_id, f.set_key,
           s.report_id, s.section_id, s.section_key, s.section_order,
           s.part, s.item, s.section_type, s.original_title,
           s.offset_start, s.offset_end, s.source_locator,
           s.section_validation_status, s.extraction_version,
           s.source_url, s.source_sha256, s.content
    FROM finance.filing f
    JOIN finance.filing_section s
      ON s.filing_id = f.filing_id
     AND s.processing_set_id = f.processing_set_id
    WHERE f.filing_id = %(filing_id)s::uuid
      AND (%(section_types)s::text[] IS NULL OR s.section_type = ANY(%(section_types)s::text[]))
    ORDER BY s.section_order
"""


# --------------------------------------------------------------------------- #
# Functions (import these from a notebook or the extraction pipeline)
# --------------------------------------------------------------------------- #

def connect() -> psycopg.Connection:
    conn = psycopg.connect(row_factory=dict_row)
    conn.read_only = True  # every transaction on this connection is read-only
    return conn


def find_filings(conn, tickers=None, accessions=None, form=None, since=None, until=None) -> pd.DataFrame:
    """
    Step 1: 
    - which filings exist;
    - which version is selected;
    - how many sections each has.
    """
    tickers = [t.upper() for t in tickers] if tickers else None   # "aapl" -> "AAPL"
    form = form.upper() if form else None
    params = {"tickers": tickers, "accessions": accessions, "form": form, "since": since, "until": until}
    df = pd.DataFrame(conn.execute(FILINGS_SQL, params).fetchall())
    if not df.empty:
        df[["fiscal_year", "fiscal_period"]] = df.apply(_fiscal_label, axis=1, result_type="expand")
        df["fiscal_year"] = df["fiscal_year"].astype("Int64")
    return df


def _fiscal_label(row) -> tuple:
    """Estimate fiscal year and period from the period-end date and the fiscal-year-end month.

    The Data team does not supply fiscal_year / fiscal_period yet (data contract item 4),
    so this is derived. Fiscal year = calendar year in which that fiscal year ends,
    e.g. Apple's quarter ending Dec 2024 is FY2025 Q1.
    """
    end, fye = row["reporting_period_end"], row["fye_month"]
    if end is None or pd.isna(fye):
        return (None, None)
    end = pd.Timestamp(end) - pd.Timedelta(days=10)
    months_after_fye = (end.month - int(fye)) % 12          # 0 = the fiscal-year-end month
    fiscal_year = end.year + (1 if end.month > int(fye) else 0)
    if row["form"] == "10-K":
        return (fiscal_year, "FY")
    return (fiscal_year, f"Q{round(months_after_fye / 3)}")


def section_inventory(conn, filing_ids, section_types=None) -> pd.DataFrame:
    """
    Step 2: section metadata (no text) for the chosen filings.
    """
    params = {"filing_ids": [str(i) for i in filing_ids], "section_types": section_types}
    return pd.DataFrame(conn.execute(INVENTORY_SQL, params).fetchall())


def fetch_sections(conn, filing_id, section_types=None) -> list[dict]:
    """
    Step 3: full section records, with text, for one filing.
    """
    params = {"filing_id": str(filing_id), "section_types": section_types}
    return conn.execute(SECTIONS_SQL, params).fetchall()


def export_sections(conn, filings: pd.DataFrame, section_types=None, out_dir: Path = OUT_DIR) -> pd.DataFrame:
    """
    Write one JSONL per filing plus a manifest of what was exported and from which version.
    """
    sec_dir = out_dir / "sections"
    sec_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for f in filings.itertuples():
        rows = fetch_sections(conn, f.filing_id, section_types)
        path = sec_dir / f"{f.ticker or f.cik}_{f.accession_number}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                r["n_chars"] = len(r["content"] or "")
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        manifest.append({
            "ticker": f.ticker, "accession_number": f.accession_number, "form": f.form,
            "filing_date": f.filing_date, "processing_set_id": f.processing_set_id,
            "set_key": f.set_key, "n_sections": len(rows), "file": str(path),
        })
        print(f"  {f.ticker} {f.form} {f.accession_number}: {len(rows)} sections -> {path}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_df = pd.DataFrame(manifest)
    manifest_path = out_dir / f"manifest_{stamp}.csv"
    manifest_df.to_csv(manifest_path, index=False)
    print(f"Manifest -> {manifest_path}")
    return manifest_df


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["filings", "inventory", "fetch"])
    p.add_argument("--tickers", nargs="+", help="e.g. AAPL MSFT JPM")
    p.add_argument("--accessions", nargs="+", help="exact accession numbers; combine with other filters")
    p.add_argument("--form", type=str.upper, choices=["10-K", "10-Q"])
    p.add_argument("--since", help="filing date from, YYYY-MM-DD")
    p.add_argument("--until", help="filing date to, YYYY-MM-DD")
    p.add_argument("--section-types", nargs="+", help="e.g. risk_factors mda business")
    p.add_argument("--latest", action="store_true", help="keep only each company's most recent matching filing")
    a = p.parse_args()

    if not (a.tickers or a.accessions):
        sys.exit("Give --tickers or --accessions so we never pull the whole database by accident.")

    with connect() as conn:
        filings = find_filings(conn, a.tickers, a.accessions, a.form, a.since, a.until)
        if filings.empty:
            sys.exit("No matching filings are loaded yet.")
        if a.latest:
            filings = filings.groupby("ticker", dropna=False, sort=False).head(1)

        empty = filings[filings.n_sections == 0]
        if not empty.empty:
            print(f"Note: {len(empty)} filing(s) have no sections yet "
                  f"(selection_reason: {', '.join(empty.selection_reason.unique())}).")

        if a.command == "filings":
            cols = ["ticker", "form", "fiscal_year", "fiscal_period", "reporting_period_end",
                    "filing_date", "accession_number", "n_sections"]
            print(filings[cols].to_string(index=False))

        elif a.command == "inventory":
            inv = section_inventory(conn, filings.filing_id, a.section_types)
            cols = ["ticker", "accession_number", "section_order", "item",
                    "section_type", "original_title", "n_chars", "section_validation_status"]
            print(inv[cols].to_string(index=False) if not inv.empty else "No sections match.")

        elif a.command == "fetch":
            export_sections(conn, filings[filings.n_sections > 0], a.section_types)


if __name__ == "__main__":
    main()