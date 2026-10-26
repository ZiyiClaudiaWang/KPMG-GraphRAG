import pandas as pd
import json
import hashlib
from decimal import Decimal, InvalidOperation

def generate_fact_id(row):
    fields = {
        "cik": row["cik"],
        "accession_number": row["accession_number"],
        "concept_namespace": row["concept_namespace"],
        "concept_name": row["concept_name"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "unit": row["unit"],
        "numeric_value": row["numeric_value"],
        "frame": row["frame"],
    }

    text = json.dumps(
        fields,
        sort_keys=True,
        default=str
    )

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def parse_xbrl_value(row):
    # Nil facts must remain NULL
    if str(row["is_nil"]).lower() == "true":
        return None

    raw = str(row["raw_value"]).strip()

    if not raw:
        return None

    # Some iXBRL formats require special handling.
    # For now, accept only simple numeric text.
    cleaned = raw.replace(",", "").replace(" ", "")

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None

    # Apply scale
    scale = row["scale"]

    if scale is not None and str(scale).strip():
        value *= Decimal(10) ** int(scale)

    # Apply XBRL sign
    if row["sign"] == "-":
        value = -value

    return str(value)


def get_segment_name(dimensions_json):
    dimensions = json.loads(dimensions_json)

    for item in dimensions:
        if "StatementBusinessSegmentsAxis" in item["axis"]:
            return item["member"]

    return None


def make_fact_id(row):
    identity = {
        "cik": row["cik"],
        "accession_number": row["accession_number"],
        "concept_name": row["concept_name"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "period_type": row["period_type"],
        "dimensions": json.loads(row["dimensions"]),
        "unit": row["unit"]
    }

    identity_text = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False
    )

    return hashlib.sha256(
        identity_text.encode("utf-8")
    ).hexdigest()


def rounding_interval(row):
    value = row["numeric_value"]

    if value is None or pd.isna(value):
        return None

    try:
        value = Decimal(str(value))
        decimals = str(row["decimals"]).strip().upper()

        if decimals == "INF":
            return (value, value)

        tolerance = Decimal(10) ** (-int(decimals)) / 2

        return (
            value - tolerance,
            value + tolerance
        )

    except (InvalidOperation, ValueError, TypeError):
        return None


def is_compatible_group(group):
    intervals = [
        rounding_interval(row)
        for _, row in group.iterrows()
    ]

    if any(interval is None for interval in intervals):
        return False

    return (
        max(interval[0] for interval in intervals)
        <= min(interval[1] for interval in intervals)
    )


    def precision_rank(value):
        if str(value).strip().upper() == "INF":
            return float("inf")

        try:
            return int(value)
        except (ValueError, TypeError):
            return float("-inf")




def load_filing_html(html_path):
    from bs4 import BeautifulSoup

    with open(html_path, "r", encoding="utf-8") as file:
        html = file.read()

    soup = BeautifulSoup(html, "lxml")
    return soup


def extract_contexts(soup):
    import json

    contexts = {}

    for context in soup.find_all("xbrli:context"):
        context_id = context.get("id")
        period = context.find("xbrli:period")

        start = period.find("xbrli:startdate") if period else None
        end = period.find("xbrli:enddate") if period else None
        instant = period.find("xbrli:instant") if period else None

        dimensions = []

        for member in context.find_all(
            ["xbrldi:explicitmember", "xbrldi:typedmember"]
        ):
            dimension = {
                "axis": member.get("dimension"),
                "member": member.get_text(strip=True)
            }

            if member.name == "xbrldi:typedmember":
                dimension["type"] = "typed"
                child = member.find(True)
                dimension["domain_tag"] = child.name if child else None
            else:
                dimension["type"] = "explicit"

            dimensions.append(dimension)

        contexts[context_id] = {
            "period_start": start.get_text(strip=True) if start else None,
            "period_end": (
                end.get_text(strip=True) if end
                else instant.get_text(strip=True) if instant
                else None
            ),
            "period_type": "duration" if start else "instant",
            "dimensions": json.dumps(dimensions, sort_keys=True)
        }

    return contexts


def extract_units(soup):
    units = {}

    for unit in soup.find_all("xbrli:unit"):
        unit_id = unit.get("id")

        numerator = unit.find("xbrli:unitnumerator")
        denominator = unit.find("xbrli:unitdenominator")

        if numerator and denominator:
            num = numerator.find("xbrli:measure")
            den = denominator.find("xbrli:measure")

            num_value = num.get_text(strip=True).split(":")[-1]
            den_value = den.get_text(strip=True).split(":")[-1]

            unit_name = f"{num_value}/{den_value}"
        else:
            measure = unit.find("xbrli:measure")
            unit_name = (
                measure.get_text(strip=True).split(":")[-1]
                if measure else None
            )

        units[unit_id] = unit_name

    return units


def extract_facts(soup, contexts, units, cik, ticker, accession_number):
    import pandas as pd

    rows = []

    for i, fact in enumerate(soup.find_all("ix:nonfraction")):
        context_ref = fact.get("contextref")
        unit_ref = fact.get("unitref")

        context = contexts.get(context_ref, {})

        row = {
            "source_row_id": i + 1,
            "cik": cik,
            "ticker": ticker,
            "accession_number": accession_number,
            "concept_name": fact.get("name"),
            "context_ref": context_ref,
            "period_start": context.get("period_start"),
            "period_end": context.get("period_end"),
            "period_type": context.get("period_type"),
            "dimensions": context.get("dimensions", "[]"),
            "raw_value": fact.get_text(strip=True),
            "unit_ref": unit_ref,
            "unit": units.get(unit_ref),
            "scale": fact.get("scale"),
            "sign": fact.get("sign"),
            "decimals": fact.get("decimals"),
            "format": fact.get("format"),
            "is_nil": fact.get("xsi:nil") == "true",
            "source_fact_id": fact.get("id")
        }

        row["numeric_value"] = parse_xbrl_value(row)
        rows.append(row)

    return pd.DataFrame(rows)


def precision_rank(value):
    if str(value).strip().upper() == "INF":
        return float("inf")

    try:
        return int(value)
    except (ValueError, TypeError):
        return float("-inf")


def deduplicate_facts(df):
    canonical_rows = []
    review_rows = []

    for fact_id, group in df.groupby("fact_id", sort=False):
        if len(group) == 1:
            canonical_rows.append(group.iloc[0])

        elif is_compatible_group(group):
            ranked = group.copy()

            ranked["_precision"] = ranked["decimals"].apply(
                precision_rank
            )

            ranked = ranked.sort_values(
                ["_precision", "source_row_id"],
                ascending=[False, True]
            )

            canonical_rows.append(
                ranked.iloc[0].drop("_precision")
            )

        else:
            review_rows.extend(
                row for _, row in group.iterrows()
            )

    canonical_df = pd.DataFrame(canonical_rows)
    review_df = pd.DataFrame(review_rows)

    return canonical_df, review_df


def process_filing(html_path, cik, ticker, accession_number):
    soup = load_filing_html(html_path)

    contexts = extract_contexts(soup)
    units = extract_units(soup)

    raw_df = extract_facts(
        soup, contexts, units,
        cik, ticker, accession_number
    )

    raw_df["fact_id"] = raw_df.apply(
        make_fact_id, axis=1
    )

    canonical_df, review_df = deduplicate_facts(raw_df)

    return raw_df, canonical_df, review_df


def save_to_duckdb(raw_df, canonical_df, review_df, db_path):
    import duckdb

    conn = duckdb.connect(db_path)

    try:
        conn.register("raw_data", raw_df)
        conn.register("canonical_data", canonical_df)
        conn.register("review_data", review_df)

        conn.execute("""
            CREATE OR REPLACE TABLE facts_raw_v1
            AS SELECT * FROM raw_data
        """)

        conn.execute("""
            CREATE OR REPLACE TABLE financial_facts_v1
            AS SELECT * FROM canonical_data
        """)

        conn.execute("""
            CREATE OR REPLACE TABLE facts_review_v1
            AS SELECT * FROM review_data
        """)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Load XBRL financial facts into DuckDB"
    )
    parser.add_argument("--html", required=True)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--accession", required=True)
    parser.add_argument("--output", required=True)

    args = parser.parse_args()

    raw, canonical, review = process_filing(
        args.html, args.cik, args.ticker, args.accession
    )

    save_to_duckdb(raw, canonical, review, args.output)

    print("Raw facts:", len(raw))
    print("Canonical facts:", len(canonical))
    print("Review rows:", len(review))
    print("Database saved:", args.output)
