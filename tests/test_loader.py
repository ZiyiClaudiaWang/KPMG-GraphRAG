"""The fact store saves numbers and dates as real types, not text (#37)."""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "structured"))
import fact_loader_v1 as loader

FILING = dict(accession_number="0001193125-26-323660", cik="0000789019",
              ticker="MSFT", company_name="MICROSOFT CORP", form="10-K",
              filing_date="2026-07-29")


def make_rows(*overrides):
    """One fact row per dict in `overrides`, every column a string, as the parser makes them."""
    base = dict(cik="0000789019", ticker="MSFT",
                accession_number=FILING["accession_number"], filing_date="2026-07-29",
                concept_name="us-gaap:Revenues", context_ref="c1",
                period_start="2025-07-01", period_end="2026-06-30", period_type="duration",
                dimensions="[]", raw_value="1", unit_ref="usd", unit="USD", scale="0",
                sign=None, decimals="0", format=None, is_nil=False,
                source_fact_id="x", numeric_value="1")
    df = pd.DataFrame([dict(base, **o) for o in overrides])
    df.insert(0, "source_row_id", range(len(df)))
    df["fact_id"] = df.apply(loader.make_fact_id, axis=1)
    return df


def build(tmp_path, df, name="store.duckdb", append=False):
    """Save df as raw and canonical, with an empty review table; return the store's path."""
    path = str(tmp_path / name)
    empty = pd.DataFrame([], columns=df.columns)
    loader.save_to_duckdb(df, df.copy(), empty, path, filing=FILING, append=append)
    return path


def test_numbers_and_dates_are_typed(tmp_path):

  
    path = build(tmp_path, make_rows({}))
    conn = duckdb.connect(path, read_only=True)
    cur = conn.cursor()
   
    types = dict(cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                             "WHERE table_name='financial_facts_v1'").fetchall())
    
    assert types["numeric_value"] == "DOUBLE"
    assert types["numeric_value"] == "DOUBLE"
    assert types["scale"] == "INTEGER"
    assert types["period_start"] == "DATE"
    assert types["period_end"] == "DATE"
    assert types["filing_date"] == "DATE"
   


    


def test_largest_value_sorts_first(tmp_path):

    

    df = make_rows(
       {"concept_name": "us-gaap:A", "numeric_value": "9"},
       {"concept_name": "us-gaap:B", "numeric_value": "281724000000"},
   )
    
    path = build(tmp_path, df)
    conn = duckdb.connect(path, read_only = True)
    cur = conn.cursor()

    check = cur.execute("SELECT numeric_value FROM financial_facts_v1 ORDER BY numeric_value DESC").fetchone()
        
    assert check[0] == 281724000000
    


def test_raw_value_and_decimals_stay_text(tmp_path):

    df = make_rows({"concept_name": "us-gaap:A", "raw_value": "three", "decimals": "INF"})
    path = build(tmp_path, df)

    conn = duckdb.connect(path, read_only = True)
    check = conn.execute("SELECT raw_value, decimals FROM financial_facts_v1").fetchall()

    assert check[0] == ('three', 'INF')
  


def test_a_bad_number_stops_the_build(tmp_path):

    df = make_rows({"concept_name": "us-gaap:A", "numeric_value": "12,3oo"})


    with pytest.raises(duckdb.ConversionException):
        build(tmp_path, df)


    
def test_appending_to_an_untyped_store_is_refused(tmp_path):
    path = str(tmp_path / "store.duckdb")
    conn = duckdb.connect(path)
    conn.execute("CREATE TABLE facts_raw_v1 (numeric_value VARCHAR)")
    conn.close()
 
    with pytest.raises(RuntimeError):
        build(tmp_path, make_rows({}), append=True)