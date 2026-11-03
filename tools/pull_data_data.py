import os 
import argparse 
from pathlib import Path 
import duckdb
import pandas as pd 
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row


#---------------------------------------------------------------------------- load


load_dotenv('.env.aws')


ACCESSION = "0001193125-26-323660"
OUT = Path("data/data_team_facts.duckdb")


def connect():
    conn = psycopg.connect(row_factory = dict_row)
    conn.read_only = True
    return conn

def query(conn, sql, params = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall())

#-------------------------------------------------------------------------- explore 

def explore(conn):
    print('\n== Tables and views in SEC & Finance')
    print(query(conn, """SELECT table_schema, table_name 
    FROM information_schema.tables 
    WHERE table_schema IN ('sec', 'finance') 
    ORDER BY table_schema, table_name""").to_string(index = False))

    print("\n== Can our login read these?")
    print(query(conn, """
        SELECT t AS table_name, has_table_privilege(t, 'SELECT') AS can_read
        FROM unnest(ARRAY['sec.financial_facts', 'sec.filings',
                          'sec.cell_fact_links', 'finance.metric_evidence']) AS t
    """).to_string(index=False))
 
    # 3. Is our filing in their database at all?
    print(f"\n== Filing {ACCESSION}")
    print(query(conn, """
        SELECT id, accession_number, form, filing_date, reporting_period_end
        FROM sec.filings
        WHERE accession_number = %s
    """, (ACCESSION,)).to_string(index=False))
 
    # 4. How many facts, per extraction version? More than one version means
    #    the filing was re-extracted, and we must pick one before comparing.
    print("\n== Facts per extraction version")
    print(query(conn, """
        SELECT ff.extraction_version, count(*) AS facts
        FROM sec.financial_facts AS ff
        JOIN sec.filings AS f ON f.id = ff.filing_id   -- link each fact to its filing
        WHERE f.accession_number = %s
        GROUP BY ff.extraction_version                  -- one row per version
        ORDER BY ff.extraction_version
    """, (ACCESSION,)).to_string(index=False))

    print("\n== Microsoft filings in Data's database")
    print(query(conn, """
        SELECT f.accession_number, f.form, f.filing_date, f.reporting_period_end,
               (SELECT count(*) FROM sec.financial_facts ff
                 WHERE ff.filing_id = f.id) AS facts
        FROM sec.filings AS f
        JOIN sec.companies AS c ON c.id = f.company_id
        WHERE c.cik = '0000789019'
        ORDER BY f.filing_date DESC
    """).to_string(index=False))
 
 
# ---------------------------------------------------------------- pull
 
PULL_SQL = """
    SELECT
        f.accession_number,
        ff.id::text              AS data_fact_id,       -- Data's random UUID
        ff.source_document_id::text,
        ff.extraction_version,
        ff.occurrence_key,                              -- with the two above: Data's natural key
        ff.concept_namespace,
        ff.concept_name,
        ff.numeric_value::text   AS numeric_value,      -- as text: keeps every digit exact
        ff.raw_value,
        ff.is_nil,
        ff.reported_decimals,
        ff.unit::text            AS unit,               -- jsonb -> text for DuckDB
        ff.period_kind,
        ff.period_start,
        ff.period_end,
        ff.instant_date,
        ff.dimensions::text      AS dimensions          -- jsonb -> text for DuckDB
    FROM sec.financial_facts AS ff
    JOIN sec.filings AS f ON f.id = ff.filing_id
    WHERE f.accession_number = %s
"""
 
 
def save(df: pd.DataFrame, path: Path = OUT):
    path.parent.mkdir(exist_ok=True)
    with duckdb.connect(str(path)) as db:
        # DuckDB can read a pandas DataFrame by its Python variable name.
        db.execute("CREATE OR REPLACE TABLE data_facts AS SELECT * FROM df")
        n = db.execute("SELECT count(*) FROM data_facts").fetchone()[0]
    print(f"saved {n} rows to {path} (table: data_facts)")
 
 
def pull(conn):
    df = query(conn, PULL_SQL, (ACCESSION,))
    if df.empty:
        raise SystemExit(f"No facts for {ACCESSION}. Run 'explore' to see why.")
    save(df)
 
 
if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["explore", "pull"])
    step = p.parse_args().step
    with connect() as conn:
        explore(conn) if step == "explore" else pull(conn)
 