"""Build the structured arm's DuckDB store from the Data team's Postgres.

A second source for the same store. `fact_loader_v1.py` parses filing HTML from
EDGAR; this reads facts the Data team has already extracted, and writes the same
three tables, so `structured_query_v1.py` runs unchanged against either one.

Why bother: their columns carry real types (`numeric_value` numeric,
`period_start` date, `dimensions` jsonb) where ours are all VARCHAR, and they
hold 660 filings where we build 20.

Two sources, because they are not the same thing:

  --source ready   sec.analysis_ready_facts. Their validated view. Today it
                   holds only `filing-import-v4` sets: 7 filings, 6,645 facts,
                   one of which is in our corpus.
  --source raw     sec.financial_facts joined to the filing. Covers all 20 of
                   our filings (26,687 facts) on sets v1 to v3. Each filing has
                   one processing set and one extraction version, so reading it
                   directly does not mix runs.

`--source` is required and has no default. `ready` is validated and covers one
filing of our twenty; `raw` covers all twenty and is unvalidated. Neither is
safe to assume, so the caller says which, and the store records the answer --
a number from an unvalidated set should not be quoted as if the Data team had
signed it off.

Run:
    python3 structured/fact_loader_data_api.py --env ~/db-test/.env.aws \
        --output corpus_facts.duckdb --accessions 0001193125-26-323660
    python3 structured/fact_loader_data_api.py --env ~/db-test/.env.aws \
        --output corpus_facts.duckdb --source raw --all-of config/filings.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

LOADER_VERSION = "data-api-loader-v1"

# Their concepts and dimensions are in Clark notation, "{namespace}LocalName".
# Ours are prefixed QNames, "us-gaap:RevenueFromContract...". Every comparison
# we make downstream -- the gold set, the segment filter, dimension scoring --
# is against the prefixed form, so the namespace has to become a prefix.
_CLARK = re.compile(r"^\{(?P<ns>[^}]*)\}(?P<local>.+)$")

# Namespaces whose prefix never appears on a concept, so it cannot be learned
# from the data. Keep this short; anything else should be discovered.
_FALLBACK_PREFIXES = {
    "http://www.xbrl.org/2003/instance": "xbrli",
    "http://www.xbrl.org/2003/iso4217": "iso4217",
    "http://www.w3.org/2001/XMLSchema": "xsd",
    # Used as a dimension member (country:US) but never as a concept, so the
    # learned map cannot see it. The year moves; match on the stem.
    "http://xbrl.sec.gov/country": "country",
}


def _stem(ns: str) -> str:
    """Drop a trailing /YYYY or /YYYYMMDD so one entry covers every year."""
    return re.sub(r"/\d{4}(\d{2}\d{2})?$", "", ns or "")


def load_env(path):
    """Read a dotenv file into the environment. psycopg reads PG* from there."""
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def connect():
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(row_factory=dict_row)
    conn.read_only = True
    return conn


def prefix_map(conn) -> dict[str, str]:
    """namespace -> prefix, learned from their own data.

    Every fact carries `concept_namespace` and a `source_reference.qname` whose
    first half is the prefix the filer used. Pairing them gives a map we did not
    have to guess. It is 1:1 across all 891,986 facts, checked before writing
    this; the query still takes the most common prefix per namespace so a single
    odd row cannot flip a mapping.
    """
    sql = """
        SELECT concept_namespace AS ns,
               split_part(source_reference->>'qname', ':', 1) AS prefix,
               count(*) AS n
        FROM sec.financial_facts
        WHERE source_reference ? 'qname' AND concept_namespace IS NOT NULL
        GROUP BY 1, 2
        ORDER BY ns, n DESC
    """
    out: dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            if row["prefix"] and row["ns"] not in out:
                out[row["ns"]] = row["prefix"]
    return {**_FALLBACK_PREFIXES, **out}


def qname(clark: str | None, prefixes: dict[str, str], unknown: set) -> str | None:
    """"{ns}Local" -> "prefix:Local". Records namespaces it cannot resolve."""
    if not clark:
        return None
    m = _CLARK.match(clark)
    if not m:
        return clark  # already a QName, or something we should not rewrite
    ns, local = m.group("ns"), m.group("local")
    prefix = prefixes.get(ns) or prefixes.get(_stem(ns))
    if prefix is None:
        unknown.add(ns)
        return local  # better a bare local name than a URI nobody can match
    return f"{prefix}:{local}"


def dimensions(raw, prefixes, unknown) -> str:
    """Their dimension list -> ours, as sorted JSON text.

    Theirs: {"kind", "member", "dimension"} in Clark notation.
    Ours:   {"axis", "member", "type"} as prefixed QNames, sorted by axis.

    Returned as text, not a DuckDB structure, because `structured_search` calls
    `json.loads()` on this column.
    """
    items = []
    for d in raw or []:
        items.append({
            "axis": qname(d.get("dimension"), prefixes, unknown),
            "member": qname(d.get("member"), prefixes, unknown),
            "type": d.get("kind") or "explicit",
        })
    items.sort(key=lambda d: (str(d["axis"]), str(d["member"])))
    return json.dumps(items, sort_keys=True, ensure_ascii=False)


def unit_text(unit) -> str | None:
    """Their unit jsonb -> the short string ours carries.

    {"numerator": ["{...iso4217}USD"], "denominator": []} -> "USD".
    A denominator makes it a rate, so keep both halves: "USD/shares".
    """
    if not unit:
        return None
    local = lambda c: (_CLARK.match(c).group("local") if _CLARK.match(c) else c)  # noqa: E731
    num = [local(x) for x in (unit.get("numerator") or [])]
    den = [local(x) for x in (unit.get("denominator") or [])]
    if not num:
        return None
    top = "*".join(num)
    return f"{top}/{'*'.join(den)}" if den else top


def make_fact_id(row) -> str:
    """The same identity hash `fact_loader_v1.make_fact_id` writes.

    Same recipe and same key order, so a fact loaded from Postgres and the same
    fact loaded from EDGAR get the same id and `comparetool.py` can join them.
    The value is deliberately not in the hash, so a restatement stays
    comparable to what it restated.
    """
    identity = {
        "cik": row["cik"],
        "accession_number": row["accession_number"],
        "concept_name": row["concept_name"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "period_type": row["period_type"],
        "dimensions": json.loads(row["dimensions"]),
        "unit": row["unit"],
    }
    text = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_READY = """
    SELECT a.*, f.filing_date, f.reporting_period_end, co.ticker, co.name AS company_name
    FROM sec.analysis_ready_facts a
    JOIN sec.filings f ON f.accession_number = a.accession_number
    JOIN sec.companies co ON co.id = f.company_id
    WHERE a.accession_number = ANY(%(accessions)s)
"""

_RAW = """
    SELECT ff.*, f.accession_number, f.form, f.filing_date, f.reporting_period_end,
           co.cik, co.ticker, co.name AS company_name
    FROM sec.financial_facts ff
    JOIN sec.filings f ON f.id = ff.filing_id
    JOIN sec.companies co ON co.id = f.company_id
    WHERE f.accession_number = ANY(%(accessions)s)
"""


def fetch(conn, accessions, source):
    with conn.cursor() as cur:
        cur.execute(_READY if source == "ready" else _RAW, {"accessions": list(accessions)})
        return cur.fetchall()


def to_rows(records, prefixes):
    """Their rows -> the columns `financial_facts_v1` holds."""
    unknown: set = set()
    rows = []
    for r in records:
        dims = dimensions(r.get("dimensions"), prefixes, unknown)
        unit = unit_text(r.get("unit"))
        ref = r.get("source_reference") or {}
        # An instant fact has no span. Ours puts the date in period_end and
        # leaves period_start null, which is what the gold set expects.
        instant = r.get("instant_date")
        concept = ref.get("qname") or qname(
            f"{{{r.get('concept_namespace')}}}{r.get('concept_name')}", prefixes, unknown)
        row = {
            "cik": str(r["cik"]).zfill(10) if r.get("cik") else None,
            "ticker": r.get("ticker"),
            "accession_number": r["accession_number"],
            "filing_date": str(r["filing_date"]) if r.get("filing_date") else None,
            "concept_name": concept,
            "context_ref": ref.get("context_ref"),
            "period_start": None if instant else _date(r.get("period_start")),
            "period_end": _date(instant or r.get("period_end")),
            "period_type": r.get("period_kind"),
            "dimensions": dims,
            "raw_value": r.get("raw_value"),
            "unit_ref": ref.get("unit_ref"),
            "unit": unit,
            "scale": None,          # not recorded upstream
            "sign": None,           # not recorded upstream
            "decimals": r.get("reported_decimals"),
            "format": None,         # not recorded upstream
            "is_nil": bool(r.get("is_nil")),
            "source_fact_id": str(r.get("id")) if r.get("id") else None,
            "numeric_value": None if r.get("numeric_value") is None else str(r["numeric_value"]),
        }
        row["fact_id"] = make_fact_id(row)
        rows.append(row)
    return rows, unknown


def _date(value):
    return str(value) if value else None


def canonicalise(rows):
    """One row per fact identity.

    A filing states the same fact more than once -- a figure in the statement
    and again in a note -- and XBRL tags both. `fact_loader_v1` keeps one, so
    this keeps one too, or the two stores would disagree on their counts.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        seen.setdefault(row["fact_id"], row)
    return list(seen.values())


def write(rows_df, filings_df, output, source, snapshot):
    """DuckDB reads `rows_df` and `filings_df` out of this function's frame, so
    the parameter names are load-bearing -- renaming them breaks the queries."""
    import duckdb

    out = Path(output).expanduser()
    conn = duckdb.connect(str(out))
    try:
        conn.execute("CREATE OR REPLACE TABLE financial_facts_v1 AS SELECT * FROM rows_df", )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filings_v1 (
                accession_number VARCHAR PRIMARY KEY,
                cik VARCHAR, ticker VARCHAR,
                company_name VARCHAR, form VARCHAR,
                filing_date VARCHAR, loader_version VARCHAR
            )""")
        conn.execute("DELETE FROM filings_v1 WHERE accession_number IN (SELECT accession_number FROM filings_df)")
        conn.execute("INSERT INTO filings_v1 SELECT accession_number, cik, ticker, company_name, form, filing_date, "
                     f"'{LOADER_VERSION}' FROM filings_df")
        conn.execute("CREATE TABLE IF NOT EXISTS store_meta_v1 (key VARCHAR PRIMARY KEY, value VARCHAR)")
        for key, value in (("corpus_version", snapshot), ("source", f"data-api:{source}"),
                           ("loader_version", LOADER_VERSION)):
            conn.execute("INSERT OR REPLACE INTO store_meta_v1 VALUES (?, ?)", [key, value])
    finally:
        conn.close()


def accessions_from(path: str) -> list[str]:
    """Every accession in a filing list, in file order.

    Reads the same `config/filings.yaml` the extraction pipeline uses, so the
    structured arm and the graph arm describe the same filings. Typing twenty
    accessions by hand is how the two drift apart.
    """
    try:
        import yaml
    except ImportError:
        raise SystemExit(
            "--all-of needs PyYAML. Install it, or pass --accessions instead."
        )

    with open(os.path.expanduser(path)) as fh:
        doc = yaml.safe_load(fh) or {}

    found = [f["accession"] for f in (doc.get("filings") or []) if f.get("accession")]
    if not found:
        raise SystemExit(f"no filings with an accession in {path}")
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default="~/db-test/.env.aws", help="dotenv holding PGHOST, PGUSER and the rest")
    p.add_argument("--output", required=True)
    # No default. `ready` used to be one, and it covers a single filing of our
    # twenty -- so the command you would reach for first built a store that
    # looked fine and held almost nothing. Neither source is the safe choice to
    # assume: `ready` is validated and nearly empty, `raw` is complete and
    # unvalidated. Make the caller say which.
    p.add_argument("--source", choices=("ready", "raw"), required=True,
                   help="ready: Data's validated view, 7 filings, 1 of ours. "
                        "raw: all 20 of ours, their unvalidated sets v1-v3")
    p.add_argument("--accessions", nargs="+", help="accession numbers to load")
    p.add_argument("--all-of", metavar="FILINGS_YAML",
                   help="load every accession in a filing list, e.g. config/filings.yaml")
    p.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    args = p.parse_args()

    if args.accessions and args.all_of:
        p.error("give --accessions or --all-of, not both")
    if args.all_of:
        args.accessions = accessions_from(args.all_of)
        print(f"{len(args.accessions)} accessions from {args.all_of}")
    if not args.accessions:
        p.error("give --accessions or --all-of")

    load_env(args.env)
    import pandas as pd  # noqa: F401  (duckdb reads the frame by name)

    conn = connect()
    try:
        prefixes = prefix_map(conn)
        records = fetch(conn, args.accessions, args.source)
    finally:
        conn.close()

    if not records:
        hint = ("analysis_ready_facts holds only filing-import-v4 sets; try --source raw."
                if args.source == "ready" else
                "check the accessions are the ones in sec.filings, and that your shell "
                "passed them as separate arguments rather than one string.")
        raise SystemExit(f"no facts for those accessions in --source {args.source}. {hint}")

    rows, unknown = to_rows(records, prefixes)
    rows = canonicalise(rows)
    filings = {}
    for r in records:
        filings[r["accession_number"]] = {
            "accession_number": r["accession_number"],
            "cik": str(r["cik"]).zfill(10) if r.get("cik") else None,
            "ticker": r.get("ticker"),
            "company_name": r.get("company_name"),
            "form": r.get("form"),
            "filing_date": str(r["filing_date"]) if r.get("filing_date") else None,
        }

    snapshot = f"data-api-{args.source}:{len(filings)}"
    print(f"{len(records)} rows -> {len(rows)} facts, {len(filings)} filings, source={args.source}")
    if unknown:
        print(f"  unresolved namespaces ({len(unknown)}), written as bare local names:")
        for ns in sorted(unknown):
            print(f"    {ns}")
    if args.dry_run:
        print("  dry run: nothing written")
        return

    import pandas as pd
    rows_df = pd.DataFrame(rows)            # noqa: F841  read by duckdb
    filings_df = pd.DataFrame(filings.values())  # noqa: F841
    write(rows_df, filings_df, args.output, args.source, snapshot)
    print(f"  wrote {args.output}")


if __name__ == "__main__":
    main()
