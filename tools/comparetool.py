import os 
import json
import re 
from decimal import Decimal, InvalidOperation
from pathlib import Path 

import duckdb
import pandas as pd 


OURS = "data/ours_msft_fy2026.duckdb"
THEIRS = "data/data_team_facts.duckdb"
OUT_DB = Path("data/fact_comparison.duckdb")
OUT_CSV = Path("data/fact_comparison.csv")

STANDARD =  {"us-gaap", "dei", "srt", "ecd", "country", "currency", "exch",
            "stpr", "naics", "sic", "invest", "cyd"}

"""Compare our XBRL facts with the Data team's, for the same filing.

Run from the repo root, after building both stores:
    python tools/pull_data_facts.py pull                              # theirs
    python structured/fact_loader_v1.py ... --output data/ours_msft_fy2026.duckdb
    python tools/compare_facts.py

Two stages:
  1. Python puts both sides into one shape (concept, period, dimensions, unit,
     value), because the two loaders write the same facts in different formats.
  2. SQL in DuckDB matches them and sorts every fact into one category.

Writes data/fact_comparison.duckdb (both cleaned tables and the result) and
data/fact_comparison.csv (every fact that did not match exactly).
Reads both stores read-only. Changes nothing in either.
"""


# ---------------------------------------------------------------- stage 1: clean

def local(name):
    """'us-gaap:ProductMember' or '{http://...}ProductMember' -> 'ProductMember'."""
    if not isinstance(name, str):
        return name
    name = re.sub(r"^\{[^}]*\}", "", name)   # Clark notation: {namespace}Name
    return name.split(":")[-1]               # prefix:Name


def prefix_from_qname(qname):
    """Ours: 'us-gaap:Revenues' -> 'us-gaap'; 'msft:Foo' -> 'ext'."""
    p = qname.split(":")[0] if ":" in qname else ""
    return p if p in STANDARD else "ext"


def prefix_from_namespace(ns):
    """Theirs: 'http://fasb.org/us-gaap/2025' -> 'us-gaap'; company URL -> 'ext'."""
    for part in str(ns).split("/"):
        if part in STANDARD:
            return part
    return "ext"


def dims_key(raw):
    """Both sides' dimension lists -> one sorted string, e.g.
    'ProductOrServiceAxis=ProductMember'. Empty list -> ''."""
    if raw is None or raw == "" or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    items = json.loads(raw) if isinstance(raw, str) else raw
    pairs = []
    for d in items:
        axis = d.get("axis") or d.get("dimension")
        if d.get("kind") == "typed" or d.get("type") == "typed":
            # Typed dimensions hold a free-form value, not a member name.
            # Data stores the whole XML snippet (<ns:Tag xmlns:..>2026</ns:Tag>);
            # Ashley stores only its text (2026). Strip tags so both read 2026.
            raw = d.get("value") if d.get("value") is not None else d.get("member")
            member = re.sub(r"<[^>]+>", "", raw or "").strip()
        else:
            member = local(d.get("member"))
        pairs.append(f"{local(axis)}={member or ''}")
    return "|".join(sorted(pairs))


def unit_key_theirs(raw):
    """{'numerator': ['{iso4217}USD'], 'denominator': ['{xbrli}shares']} -> 'USD/shares'."""
    if raw is None or raw == "" or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    u = json.loads(raw) if isinstance(raw, str) else raw
    num = "*".join(local(x) for x in u.get("numerator", []))
    den = "*".join(local(x) for x in u.get("denominator", []))
    return f"{num}/{den}" if den else num


def unit_key_ours(raw):
    """Ours is already short ('USD', maybe 'USD/shares'); just tidy it."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    return "/".join(local(p.strip()) for p in str(raw).split("/"))


def value_key(raw):
    """'64696000000', '64696000000.0' and '6.4696E10' all -> '64696000000'.
    Kept as text so no digits are lost. Non-numbers -> None."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        return None
    try:
        d = Decimal(str(raw).strip())
    except InvalidOperation:
        return None
    if d.is_nan() or d.is_infinite():
        return None
    return format(d.normalize(), "f")


def as_date(raw):
    """Text or date -> 'YYYY-MM-DD', or None."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or pd.isna(raw):
        return None
    return pd.Timestamp(raw).strftime("%Y-%m-%d")


def clean_ours(df):
    instant = df["period_type"].str.lower().eq("instant")
    return pd.DataFrame({
        "prefix":   df["concept_name"].map(prefix_from_qname),
        "concept":  df["concept_name"].map(local),
        "start":    df["period_start"].where(~instant).map(as_date),
        "end":      df["period_end"].map(as_date),
        "dims":     df["dimensions"].map(dims_key),
        "unit":     df["unit"].map(unit_key_ours),
        "value":    df["numeric_value"].map(value_key),
        "source_id": df["fact_id"],                    # Ashley's fact_id
    })


def clean_theirs(df):
    instant = df["period_kind"].eq("instant")
    end = df["instant_date"].where(instant, df["period_end"])
    return pd.DataFrame({
        "prefix":   df["concept_namespace"].map(prefix_from_namespace),
        "concept":  df["concept_name"],
        "start":    df["period_start"].where(~instant).map(as_date),
        "end":      end.map(as_date),
        "dims":     df["dimensions"].map(dims_key),
        "unit":     df["unit"].map(unit_key_theirs),
        "value":    df["numeric_value"].map(value_key),
        # Data's natural key, as one string.
        "source_id": df["source_document_id"] + "|" + df["extraction_version"]
                     + "|" + df["occurrence_key"],
    })


# ---------------------------------------------------------------- stage 2: compare

# The identity of a fact: what it measures (prefix + concept), when (start,
# end) and for what slice (dims). Unit and value are what we then check.
# GROUP BY treats NULLs as equal, so instant facts (no start) group correctly.

COMPARE_SQL = """
-- 1. Collapse each side to one row per fact. A filing can state the same fact
--    several times (e.g. in a table and again in a note); count those repeats
--    and how many DIFFERENT values they carry. More than one is a conflict.
CREATE OR REPLACE TABLE ours_k AS
SELECT prefix, concept, start, "end", dims,
       count(*)                 AS copies,
       count(DISTINCT value)    AS n_values,
       min(value)               AS value,
       min(unit)                AS unit,
       min(source_id)           AS source_id
FROM ours GROUP BY prefix, concept, start, "end", dims;

CREATE OR REPLACE TABLE theirs_k AS
SELECT prefix, concept, start, "end", dims,
       count(*)                 AS copies,
       count(DISTINCT value)    AS n_values,
       min(value)               AS value,
       min(unit)                AS unit,
       min(source_id)           AS source_id
FROM theirs GROUP BY prefix, concept, start, "end", dims;

-- 2. FULL OUTER JOIN keeps every fact from BOTH sides: matched pairs side by
--    side, and unmatched facts with NULLs on the side that lacks them.
--    IS NOT DISTINCT FROM is '=' that also treats NULL = NULL as a match,
--    which instant facts (no start date) need.
CREATE OR REPLACE TABLE comparison AS
SELECT
    coalesce(o.prefix, t.prefix)   AS prefix,
    coalesce(o.concept, t.concept) AS concept,
    coalesce(o.start, t.start)     AS start,
    coalesce(o."end", t."end")     AS "end",
    coalesce(o.dims, t.dims)       AS dims,
    o.value AS ours_value,  t.value AS theirs_value,
    o.unit  AS ours_unit,   t.unit  AS theirs_unit,
    o.source_id AS ours_fact_id, t.source_id AS theirs_natural_key,
    CASE
        WHEN t.concept IS NULL                          THEN 'only ours'
        WHEN o.concept IS NULL AND t.value IS NULL      THEN 'only theirs (no number)'
        WHEN o.concept IS NULL                          THEN 'only theirs'
        WHEN o.n_values > 1 OR t.n_values > 1           THEN 'conflict within a side'
        WHEN o.value IS NOT DISTINCT FROM t.value
         AND o.unit  IS NOT DISTINCT FROM t.unit        THEN 'match'
        WHEN o.value IS NOT DISTINCT FROM t.value       THEN 'same value, unit differs'
        ELSE 'value differs'
    END AS result
FROM ours_k AS o
FULL OUTER JOIN theirs_k AS t
  ON  o.prefix  = t.prefix
  AND o.concept = t.concept
  AND o.start   IS NOT DISTINCT FROM t.start
  AND o."end"   IS NOT DISTINCT FROM t."end"
  AND o.dims    = t.dims;
"""


def main():
    with duckdb.connect(OURS, read_only=True) as c:
        ours_raw = c.sql("SELECT * FROM financial_facts_v1").fetchdf()
    with duckdb.connect(THEIRS, read_only=True) as c:
        theirs_raw = c.sql("SELECT * FROM data_facts").fetchdf()

    ours, theirs = clean_ours(ours_raw), clean_theirs(theirs_raw)

    if OUT_DB.exists():
        OUT_DB.unlink()                      # fresh every run
    with duckdb.connect(str(OUT_DB)) as db:
        db.execute("CREATE TABLE ours AS SELECT * FROM ours")
        db.execute("CREATE TABLE theirs AS SELECT * FROM theirs")
        db.execute(COMPARE_SQL)

        print(f"ours:   {len(ours):>5} facts in, "
              f"{db.sql('SELECT count(*) FROM ours_k').fetchall()[0][0]:>5} after merging repeats")
        print(f"theirs: {len(theirs):>5} facts in, "
              f"{db.sql('SELECT count(*) FROM theirs_k').fetchall()[0][0]:>5} after merging repeats\n")

        print("== Result")
        print(db.sql("""
            SELECT result, count(*) AS facts,
                   round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
            FROM comparison GROUP BY result ORDER BY facts DESC
        """).fetchdf().to_string(index=False))

        print("\n== Unmatched, by taxonomy")
        print(db.sql("""
            SELECT result, prefix, count(*) AS facts
            FROM comparison
            WHERE result LIKE 'only%'
            GROUP BY ALL ORDER BY result, facts DESC
        """).fetchdf().to_string(index=False))

        for category in ("value differs", "same value, unit differs",
                         "conflict within a side", "only ours", "only theirs"):
            sample = db.sql(f"""
                SELECT concept, start, "end", dims, ours_value, theirs_value,
                       ours_unit, theirs_unit
                FROM comparison WHERE result = '{category}'
                ORDER BY concept LIMIT 5
            """).fetchdf()
            if len(sample):
                print(f"\n== Examples: {category}")
                print(sample.to_string(index=False, max_colwidth=60))

        db.sql("SELECT * FROM comparison WHERE result <> 'match' ORDER BY result, concept") \
          .fetchdf().to_csv(OUT_CSV, index=False)
    print(f"\nEvery non-match: {OUT_CSV}\nFull tables: {OUT_DB}")


if __name__ == "__main__":
    main()
