"""What is actually loaded in each arm, and whether it holds together.

Our failures this month have mostly not been bad retrieval. They have been
nobody being able to see what is loaded: a graph with no subsidiaries, two fact
stores differing by three thousand facts, a default store holding one filing.
Corpus totals showed nothing wrong in any of those cases. This prints the
numbers that would have.

    python3 tools/corpus_report.py                         the internal graph
    python3 tools/corpus_report.py --instance canonical     what Eval is given
    python3 tools/corpus_report.py --json report.json       machine readable
    python3 tools/corpus_report.py --compare canonical      diff two instances

Two rules this file follows, because they are the ones that bite:

  - Every graph count filters on pipeline_scope. Counting unfiltered is correct
    today and breaks silently the first time someone loads a second extraction
    run beside the first: Claim 10,774 becomes about 21,000 with nothing saying
    why. The scope defaults to the only one present and the report shouts when
    there is more than one.
  - Nothing here is a number on its own. Every report carries the provenance of
    where it came from - instance, scope, store path, corpus version, source.
    Most of this month's confusion was two correct stores, not one wrong one.

No streamlit import. The dashboard renders what this returns, pytest asserts on
it, and the deck builder reads it, so it has to stay importable from all three.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

# Which env file describes which graph. Instance 1 is canonical and is what Eval
# and Agents are given; instance 2 is the team's internal graph and is where we
# look first.
INSTANCES = {
    "internal": ROOT / ".env.internal",
    "canonical": ROOT / "extraction_pipeline" / ".env",
}

# A filing should carry all of these. Revenue and cash are families because
# filers report them under different concepts, so a single name would show a
# false red.
KEY_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "net income": ("us-gaap:NetIncomeLoss",),
    "assets": ("us-gaap:Assets",),
    "cash": (
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "operating income": ("us-gaap:OperatingIncomeLoss",),
}

# Columns worth reporting population for. The data-API store writes three of
# these as entirely NULL, which is how we found that it does.
WATCHED_COLUMNS = ("numeric_value", "unit", "scale", "sign", "format", "decimals")


# --------------------------------------------------------------------------
# environment


def read_env_file(path: Path) -> dict[str, str]:
    """The KEY=value lines of an env file, as a dict.

    Returned rather than pushed into os.environ so credentials do not leak into
    anything this process goes on to start.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def resolve_env(instance: str, env_file: str | None) -> dict[str, str]:
    path = Path(env_file).expanduser() if env_file else INSTANCES[instance]
    values = read_env_file(path)
    if not values.get("NEO4J_URI"):
        raise SystemExit(
            f"No NEO4J_URI in {path}. Copy the instance credentials in, or pass "
            f"--env-file. Known instances: {', '.join(INSTANCES)}."
        )
    return values


# --------------------------------------------------------------------------
# graph arm


def graph_scopes(run: Callable) -> list[dict]:
    """Every pipeline_scope in the database, largest first."""
    return run(
        "MATCH (n) WHERE n.pipeline_scope IS NOT NULL "
        "RETURN n.pipeline_scope AS scope, count(*) AS nodes ORDER BY nodes DESC"
    )


def pick_scope(scopes: list[dict], requested: str | None) -> tuple[str | None, list[str]]:
    """The scope to report on, and anything the reader has to be told about it."""
    names = [s["scope"] for s in scopes]
    if requested:
        if requested not in names:
            raise SystemExit(f"No scope {requested!r} in this graph. Present: {', '.join(names) or 'none'}")
        chosen = requested
    elif len(names) == 1:
        chosen = names[0]
    elif not names:
        return None, ["This graph has no pipeline_scope on any node."]
    else:
        chosen = names[0]

    warnings = []
    if len(names) > 1:
        others = ", ".join(n for n in names if n != chosen)
        warnings.append(
            f"MORE THAN ONE SCOPE. Reporting on {chosen!r}; also present: {others}. "
            "Counts below cover one run, not the database."
        )
    return chosen, warnings


def graph_counts(run: Callable, scope: str) -> dict:
    labels = run(
        "CALL db.labels() YIELD label "
        "CALL (label) { MATCH (n) WHERE label IN labels(n) AND n.pipeline_scope = $scope "
        "RETURN count(n) AS c } "
        "RETURN label, c ORDER BY c DESC",
        scope=scope,
    )
    rels = run(
        "CALL db.relationshipTypes() YIELD relationshipType AS t "
        "CALL (t) { MATCH ()-[x]->() WHERE type(x) = t AND x.pipeline_scope = $scope "
        "RETURN count(x) AS c } "
        "RETURN t AS type, c ORDER BY c DESC",
        scope=scope,
    )
    return {
        "nodes_by_label": {r["label"]: r["c"] for r in labels if r["c"]},
        "relationships_by_type": {r["type"]: r["c"] for r in rels if r["c"]},
    }


def graph_orphans(run: Callable, scope: str) -> dict:
    """Nodes that should be attached to something and are not.

    At twenty filings these are all zero or obvious. At two hundred they are
    where recall quietly dies, because an unattached Claim is unreachable by
    every query the arm makes.
    """
    checks = {
        "claims_without_support": "MATCH (c:Claim) WHERE c.pipeline_scope = $scope "
        "AND NOT ()-[:SUPPORTS]->(c) AND NOT (c)-[:SUPPORTS]->() RETURN count(c) AS c",
        "sections_without_claims": "MATCH (s:Section) WHERE s.pipeline_scope = $scope "
        "AND NOT (s)-[:SUPPORTS]->(:Claim) AND NOT (s)<-[:SUPPORTS]-(:Claim) RETURN count(s) AS c",
        "filings_without_sections": "MATCH (f:Filing) WHERE f.pipeline_scope = $scope "
        "AND NOT (f)-[:CONTAINS]->(:Section) RETURN count(f) AS c",
        "risks_without_claims": "MATCH (r:RiskFactor) WHERE r.pipeline_scope = $scope "
        "AND NOT ()-[:CONCERNS_RISK]->(r) RETURN count(r) AS c",
        "subsidiaries_without_parent": "MATCH (s:Subsidiary) WHERE s.pipeline_scope = $scope "
        "AND NOT ()-[:OWNS]->(s) RETURN count(s) AS c",
        "companies_without_filings": "MATCH (c:Company) WHERE c.pipeline_scope = $scope "
        "AND NOT (c)-[:FILES]->(:Filing) RETURN count(c) AS c",
    }
    out = {}
    for name, cypher in checks.items():
        rows = run(cypher, scope=scope)
        out[name] = rows[0]["c"] if rows else 0
    return out


def graph_spread(run: Callable, scope: str) -> dict:
    """How unevenly the extractor filled things.

    A section holding four hundred claims beside one holding two means the two
    were treated differently, and no total shows it.
    """
    per_section = run(
        "MATCH (c:Claim) WHERE c.pipeline_scope = $scope AND c.section_id IS NOT NULL "
        "RETURN c.section_id AS id, count(*) AS c ORDER BY c DESC",
        scope=scope,
    )
    per_filing = run(
        "MATCH (c:Claim) WHERE c.pipeline_scope = $scope AND c.accession_number IS NOT NULL "
        "RETURN c.accession_number AS accession, count(*) AS c ORDER BY c DESC",
        scope=scope,
    )
    per_risk = run(
        "MATCH (:Claim)-[:CONCERNS_RISK]->(r:RiskFactor) WHERE r.pipeline_scope = $scope "
        "RETURN r.name AS risk, count(*) AS c ORDER BY c DESC",
        scope=scope,
    )
    return {
        "claims_per_section": _spread([r["c"] for r in per_section]),
        "claims_per_filing": {r["accession"]: r["c"] for r in per_filing},
        "claims_per_risk_theme": {r["risk"]: r["c"] for r in per_risk},
    }


def _spread(values: list[int]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"n": len(ordered), "min": ordered[0], "median": median, "max": ordered[-1],
            "total": sum(ordered)}


def subsidiary_coverage(run: Callable, scope: str) -> dict:
    """10-Ks that should list subsidiaries, against parents that have any.

    Only a 10-K carries an Exhibit 21, so a 10-Q with no subsidiaries is not a
    gap. Reported as coverage rather than a count because the count on its own
    cannot tell those two apart.
    """
    rows = run(
        "MATCH (f:Filing) WHERE f.pipeline_scope = $scope AND toUpper(f.form) = '10-K' "
        "RETURN count(f) AS ten_ks",
        scope=scope,
    )
    ten_ks = rows[0]["ten_ks"] if rows else 0
    rows = run(
        "MATCH (c:Company)-[:OWNS]->(s:Subsidiary) WHERE c.pipeline_scope = $scope "
        "RETURN c.ticker AS ticker, count(DISTINCT s) AS subsidiaries ORDER BY subsidiaries DESC",
        scope=scope,
    )
    per_parent = {r["ticker"]: r["subsidiaries"] for r in rows}
    return {"ten_k_filings": ten_ks, "parents_with_subsidiaries": len(per_parent),
            "subsidiaries_per_parent": per_parent}


def graph_identity(run: Callable, scope: str) -> dict:
    """What would make the arm answer about the wrong company."""
    rows = run(
        "MATCH (c:Company) WHERE c.pipeline_scope = $scope "
        "RETURN count(c) AS total, "
        "count(CASE WHEN c.ticker IS NULL OR c.ticker = '' THEN 1 END) AS no_ticker, "
        "count(CASE WHEN c.name IS NULL OR c.name = '' THEN 1 END) AS no_name",
        scope=scope,
    )
    base = dict(rows[0]) if rows else {"total": 0, "no_ticker": 0, "no_name": 0}
    variants = run(
        "MATCH (c:Company) WHERE c.pipeline_scope = $scope AND c.name IS NOT NULL "
        "WITH c.cik AS cik, collect(DISTINCT c.name) AS names "
        "WHERE size(names) > 1 RETURN cik, names",
        scope=scope,
    )
    base["ciks_with_multiple_names"] = {r["cik"]: r["names"] for r in variants}
    return base


def graph_status(run: Callable, scope: str) -> dict:
    """Extraction statuses other than ok, by label."""
    out = {}
    for label in ("Claim", "Subsidiary", "RiskFactor"):
        rows = run(
            f"MATCH (n:{label}) WHERE n.pipeline_scope = $scope AND n.extraction_status IS NOT NULL "
            "RETURN n.extraction_status AS status, count(*) AS c ORDER BY c DESC",
            scope=scope,
        )
        if rows:
            out[label] = {r["status"]: r["c"] for r in rows}
    return out


def graph_report(run: Callable, requested_scope: str | None) -> dict:
    scopes = graph_scopes(run)
    scope, warnings = pick_scope(scopes, requested_scope)
    report: dict = {
        "scopes": {s["scope"]: s["nodes"] for s in scopes},
        "scope": scope,
        "warnings": warnings,
    }
    if scope is None:
        return report
    report.update(graph_counts(run, scope))
    report["orphans"] = graph_orphans(run, scope)
    report["spread"] = graph_spread(run, scope)
    report["subsidiaries"] = subsidiary_coverage(run, scope)
    report["identity"] = graph_identity(run, scope)
    report["extraction_status"] = graph_status(run, scope)
    return report


# --------------------------------------------------------------------------
# structured arm


def store_meta(con) -> dict:
    """store_meta_v1 as a dict, which is the only way to tell two stores apart.

    The EDGAR loader wrote corpus_version alone, so you identified it by what
    was missing. It is deprecated as of 13 Nov; this reports whatever is there
    and says plainly when source is absent.
    """
    tables = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
    if "store_meta_v1" not in tables:
        return {"__missing__": "no store_meta_v1 table"}
    return {k: v for k, v in con.execute("SELECT key, value FROM store_meta_v1").fetchall()}


def store_totals(con) -> dict:
    row = con.execute(
        "SELECT count(*) AS facts, count(DISTINCT accession_number) AS filings, "
        "count(DISTINCT cik) AS companies, count(DISTINCT concept_name) AS concepts "
        "FROM financial_facts_v1"
    ).fetchone()
    return dict(zip(("facts", "filings", "companies", "concepts"), row))


def store_per_filing(con) -> list[dict]:
    """One row per filing. Totals hide a filing that loaded thin or broke.

    Nine of twenty filings failed once because an empty first filing typed every
    column NULL, and the corpus total looked healthy throughout.
    """
    rows = con.execute(
        "SELECT f.accession_number AS accession, f.ticker, f.form, f.filing_date, "
        "       count(x.fact_id) AS facts, "
        "       count(DISTINCT x.concept_name) AS concepts, "
        "       count(DISTINCT x.period_end) AS periods, "
        "       count(CASE WHEN x.dimensions IS NOT NULL AND x.dimensions NOT IN ('', '[]') "
        "                  THEN 1 END) AS dimensioned "
        "FROM filings_v1 f LEFT JOIN financial_facts_v1 x "
        "  ON x.accession_number = f.accession_number "
        "GROUP BY 1, 2, 3, 4 ORDER BY f.ticker"
    ).fetchall()
    out = []
    for accession, ticker, form, filing_date, facts, concepts, periods, dimensioned in rows:
        out.append({
            "accession": accession, "ticker": ticker, "form": form,
            "filing_date": filing_date, "facts": facts, "concepts": concepts,
            "periods": periods, "dimensioned": dimensioned,
            "dimensioned_share": round(dimensioned / facts, 3) if facts else None,
        })
    return out


def store_column_population(con) -> dict:
    """How full each watched column is.

    A column that is entirely NULL gets no type from DuckDB and reads as "NULL",
    which is the same shape as the bug where an empty first filing defined the
    schema for every filing after it.
    """
    total = con.execute("SELECT count(*) FROM financial_facts_v1").fetchone()[0]
    present = {c[0] for c in con.execute("DESCRIBE financial_facts_v1").fetchall()}
    out: dict[str, dict] = {}
    for column in WATCHED_COLUMNS:
        if column not in present:
            out[column] = {"absent": True}
            continue
        filled = con.execute(f"SELECT count({column}) FROM financial_facts_v1").fetchone()[0]
        out[column] = {
            "filled": filled,
            "share": round(filled / total, 3) if total else None,
            "all_null": filled == 0,
        }

    # A missing numeric_value is usually correct, not damage: about an eighth of
    # the store is text blocks, accounting policies and dei document metadata,
    # which have no number to hold. What is wrong is a raw_value that parses as
    # a number against an empty numeric_value, so that is counted separately.
    out["__numeric__"] = {
        "no_numeric_value": con.execute(
            "SELECT count(*) FROM financial_facts_v1 WHERE numeric_value IS NULL"
        ).fetchone()[0],
        "unparsed_numbers": con.execute(
            "SELECT count(*) FROM financial_facts_v1 "
            "WHERE numeric_value IS NULL AND try_cast(raw_value AS DOUBLE) IS NOT NULL"
        ).fetchone()[0],
    }
    return out


def store_key_concepts(con) -> dict:
    """Which filings are missing a figure every filing should have.

    "Does every filing have revenue" is the first thing a client asks and the
    cheapest smoke test we have. One red cell at two hundred filings is a bug
    that otherwise surfaces as a failed eval question weeks later.
    """
    filings = [r[0] for r in con.execute(
        "SELECT accession_number FROM filings_v1 ORDER BY ticker").fetchall()]
    matrix: dict[str, dict[str, bool]] = {}
    for name, concepts in KEY_CONCEPTS.items():
        placeholders = ", ".join("?" for _ in concepts)
        rows = con.execute(
            f"SELECT DISTINCT accession_number FROM financial_facts_v1 "
            f"WHERE concept_name IN ({placeholders})",
            list(concepts),
        ).fetchall()
        have = {r[0] for r in rows}
        matrix[name] = {a: (a in have) for a in filings}
    missing = {
        name: sorted(a for a, ok in row.items() if not ok)
        for name, row in matrix.items()
    }
    return {"present": matrix, "missing": {k: v for k, v in missing.items() if v}}


def store_dimension_mix(con, limit: int = 15) -> dict:
    """Consolidated against dimensioned, per concept, and the axes in play.

    Both live ranking bugs are segment-shaped: segment slices outscoring the
    consolidated figure and pushing it past top_k, and a segment slice standing
    in for a company total.
    """
    rows = con.execute(
        "SELECT concept_name, "
        "  count(CASE WHEN dimensions IS NULL OR dimensions IN ('', '[]') THEN 1 END) AS consolidated, "
        "  count(CASE WHEN dimensions IS NOT NULL AND dimensions NOT IN ('', '[]') THEN 1 END) AS dimensioned "
        "FROM financial_facts_v1 GROUP BY 1 HAVING dimensioned > 0 "
        "ORDER BY dimensioned DESC LIMIT ?",
        [limit],
    ).fetchall()
    axes = con.execute(
        "SELECT json_extract_string(d.axis, '$.axis') AS axis, count(*) AS c "
        "FROM financial_facts_v1 f, "
        "     LATERAL (SELECT unnest(from_json(f.dimensions, '[\"JSON\"]')) AS axis) d "
        "WHERE f.dimensions IS NOT NULL AND f.dimensions NOT IN ('', '[]') "
        "GROUP BY 1 ORDER BY c DESC LIMIT ?",
        [limit],
    ).fetchall()
    return {
        "by_concept": [
            {"concept": c, "consolidated": cons, "dimensioned": dim}
            for c, cons, dim in rows
        ],
        "axes": {a: c for a, c in axes},
    }


def store_duplicates(con) -> dict:
    """Two unrelated things wear the word duplicate, so they are split here.

    A fact_id appearing twice is a loader bug and should be zero. The same
    economic fact in two filings is a comparative - last year's figure restated
    inside this year's report - which is expected and must not be deduped away.
    The pairs worth looking at are comparatives whose value disagrees: those are
    restatements, and nobody has looked at them.
    """
    repeated = con.execute(
        "SELECT count(*) FROM (SELECT fact_id FROM financial_facts_v1 "
        "GROUP BY 1 HAVING count(*) > 1)"
    ).fetchone()[0]
    statement = con.execute(
        "SELECT count(*) FROM (SELECT accession_number, concept_name, period_start, "
        "period_end, dimensions FROM financial_facts_v1 "
        "GROUP BY 1, 2, 3, 4, 5 HAVING count(DISTINCT context_ref) > 1)"
    ).fetchone()[0]
    comparatives = con.execute(
        "SELECT count(*) FROM (SELECT cik, concept_name, period_start, period_end, "
        "period_type, dimensions FROM financial_facts_v1 "
        "GROUP BY 1, 2, 3, 4, 5, 6 HAVING count(DISTINCT accession_number) > 1)"
    ).fetchone()[0]
    restated = con.execute(
        "SELECT count(*) FROM (SELECT cik, concept_name, period_start, period_end, "
        "period_type, dimensions FROM financial_facts_v1 WHERE numeric_value IS NOT NULL "
        "GROUP BY 1, 2, 3, 4, 5, 6 "
        "HAVING count(DISTINCT accession_number) > 1 AND count(DISTINCT numeric_value) > 1)"
    ).fetchone()[0]
    return {
        "repeated_fact_id": repeated,
        "statement_duplicates": statement,
        "comparative_groups": comparatives,
        "restated_groups": restated,
    }


def store_date_checks(con) -> dict:
    """Four cheap date checks. The last one bears on the period ranking bug."""
    def count(where: str) -> int:
        return con.execute(f"SELECT count(*) FROM financial_facts_v1 WHERE {where}").fetchone()[0]

    return {
        "start_after_end": count(
            "try_cast(period_start AS DATE) > try_cast(period_end AS DATE)"),
        "duration_typed_instant": count(
            "period_type = 'instant' AND period_start IS NOT NULL AND period_start <> '' "
            "AND period_start <> period_end"),
        "missing_period_end": count("period_end IS NULL OR period_end = ''"),
        # Over a year, not over a quarter. A 10-Q legitimately carries six and
        # nine month year-to-date figures - about 1,765 of them here - so a
        # threshold near one quarter flags normal filings as broken. Only a span
        # longer than a year is actually wrong on a quarterly report.
        "quarterly_filing_over_a_year": con.execute(
            "SELECT count(*) FROM financial_facts_v1 x JOIN filings_v1 f "
            "  ON f.accession_number = x.accession_number "
            "WHERE f.form = '10-Q' AND x.period_type = 'duration' "
            "  AND date_diff('day', try_cast(x.period_start AS DATE), "
            "                try_cast(x.period_end AS DATE)) > 370"
        ).fetchone()[0],
    }


def store_report(con) -> dict:
    return {
        "meta": store_meta(con),
        "totals": store_totals(con),
        "per_filing": store_per_filing(con),
        "column_population": store_column_population(con),
        "key_concepts": store_key_concepts(con),
        "dimension_mix": store_dimension_mix(con),
        "duplicates": store_duplicates(con),
        "dates": store_date_checks(con),
    }


# --------------------------------------------------------------------------
# cross arm


def cross_arm(graph: dict, store: dict) -> list[dict]:
    """One row per filing, per arm. A filing in one arm and not the other makes
    eval scores incomparable, and nothing surfaces that today."""
    claims = (graph.get("spread") or {}).get("claims_per_filing", {})
    rows = []
    for filing in store.get("per_filing", []):
        accession = filing["accession"]
        rows.append({
            "accession": accession,
            "ticker": filing["ticker"],
            "facts": filing["facts"],
            "claims": claims.get(accession, 0),
            "chunks": None,  # RAG's index is in another repo and we cannot reach it
        })
    for accession, count in claims.items():
        if not any(r["accession"] == accession for r in rows):
            rows.append({"accession": accession, "ticker": None, "facts": 0,
                         "claims": count, "chunks": None})
    return rows


# --------------------------------------------------------------------------
# rendering


def render(report: dict) -> str:
    out: list[str] = []
    add = out.append

    add("=" * 78)
    add("CORPUS REPORT")
    add("=" * 78)
    for line in report["provenance"]:
        add(f"  {line}")
    add("")

    graph = report.get("graph") or {}
    for warning in graph.get("warnings", []):
        add(f"  !! {warning}")
        add("")

    if graph.get("scope"):
        add("GRAPH ARM")
        add("  nodes " + " · ".join(f"{k} {v:,}" for k, v in graph["nodes_by_label"].items()))
        add("  edges " + " · ".join(f"{k} {v:,}" for k, v in graph["relationships_by_type"].items()))
        subs = graph["subsidiaries"]
        add(f"  subsidiary coverage: {subs['parents_with_subsidiaries']} parents with "
            f"subsidiaries, {subs['ten_k_filings']} 10-K filings")
        orphans = {k: v for k, v in graph["orphans"].items() if v}
        add(f"  orphans: {orphans if orphans else 'none'}")
        spread = graph["spread"]["claims_per_section"]
        if spread.get("n"):
            add(f"  claims per section: min {spread['min']} · median {spread['median']} "
                f"· max {spread['max']} over {spread['n']} sections")
        identity = graph["identity"]
        add(f"  companies {identity['total']} · no ticker {identity['no_ticker']} "
            f"· no name {identity['no_name']} "
            f"· ciks with >1 name {len(identity['ciks_with_multiple_names'])}")
        add("")

    store = report.get("store") or {}
    if store:
        totals = store["totals"]
        add("STRUCTURED ARM")
        add(f"  {totals['filings']} filings · {totals['companies']} companies · "
            f"{totals['facts']:,} facts · {totals['concepts']:,} concepts")

        columns = store["column_population"]
        numeric = columns.get("__numeric__", {})
        empty = [c for c, v in columns.items() if c != "__numeric__" and v.get("all_null")]
        if empty:
            add(f"  columns entirely NULL: {', '.join(empty)}")
        if numeric:
            add(f"  no numeric_value {numeric['no_numeric_value']:,} "
                f"(text blocks, policies and document metadata) · "
                f"of those, {numeric['unparsed_numbers']:,} do hold a parseable number "
                f"and should not be empty")

        missing = store["key_concepts"]["missing"]
        if missing:
            for concept, accessions in missing.items():
                add(f"  MISSING {concept}: {len(accessions)} filings")
        else:
            add("  every filing has all five key concepts")

        dups = store["duplicates"]
        add(f"  repeated fact_id {dups['repeated_fact_id']} (should be 0) · "
            f"statement duplicates {dups['statement_duplicates']:,} · "
            f"comparative groups {dups['comparative_groups']:,} · "
            f"restated {dups['restated_groups']:,}")
        dates = store["dates"]
        add("  dates: " + " · ".join(f"{k} {v:,}" for k, v in dates.items()))
        add("")

        thinnest = sorted(store["per_filing"], key=lambda r: r["facts"])[:3]
        add("  thinnest filings")
        for row in thinnest:
            add(f"    {row['ticker']:<6} {row['form']:<5} {row['facts']:>6,} facts · "
                f"{row['concepts']:>4} concepts · {row['periods']:>3} periods")
        add("")

    rows = report.get("cross_arm") or []
    gaps = [r for r in rows if not r["facts"] or not r["claims"]]
    if rows:
        add("CROSS ARM")
        add(f"  {len(rows)} filings · {len(gaps)} present in only one arm")
        for row in gaps[:10]:
            add(f"    {row['ticker'] or '?':<6} {row['accession']}  "
                f"facts {row['facts']:>6,} · claims {row['claims']:>6,}")
        add("")

    return "\n".join(out)


def provenance_lines(instance: str, env: dict, graph: dict, store: dict, store_path: str) -> list[str]:
    """Where every number below came from. This matters more than the counts."""
    lines = [
        f"graph instance : {instance} · {env.get('AURA_INSTANCENAME') or env.get('AURA_INSTANCEID') or 'unnamed'}",
        f"pipeline_scope : {graph.get('scope')} "
        f"({len(graph.get('scopes') or {})} scope(s) in the database)",
        f"store path     : {store_path or 'not configured'}",
    ]
    meta = store.get("meta") or {}
    if meta:
        lines.append("store meta     : " + " · ".join(f"{k}={v}" for k, v in meta.items()))
        if "source" not in meta:
            lines.append("                 no source key - this is the deprecated EDGAR store")
        elif str(meta.get("source", "")).endswith(":raw"):
            lines.append("                 source is the UNVALIDATED set, not the validated view")
    return lines


# --------------------------------------------------------------------------
# entry point


def build(instance: str, env_file: str | None, scope: str | None,
          skip_graph: bool = False, skip_store: bool = False) -> dict:
    env = resolve_env(instance, env_file) if not skip_graph else {}

    graph: dict = {}
    if not skip_graph:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
        # Aura cold-starts. The first call after idle raises, so wake it before
        # asking it anything or the report reads as an outage.
        driver.verify_connectivity()
        try:
            with driver.session(database=env.get("NEO4J_DATABASE")) as session:
                def run(cypher: str, **params):
                    return [record.data() for record in session.run(cypher, **params)]

                graph = graph_report(run, scope)
        finally:
            driver.close()

    store: dict = {}
    store_path = env.get("MSFT_FACT_DB_PATH") or os.environ.get("MSFT_FACT_DB_PATH", "")
    if not skip_store and store_path:
        import duckdb

        con = duckdb.connect(str(Path(store_path).expanduser()), read_only=True)
        try:
            store = store_report(con)
        finally:
            con.close()

    report = {
        "instance": instance,
        "graph": graph,
        "store": store,
        "cross_arm": cross_arm(graph, store) if graph and store else [],
    }
    report["provenance"] = provenance_lines(instance, env, graph, store, store_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance", choices=sorted(INSTANCES), default="internal",
                        help="which graph to read (default: internal, the team's own)")
    parser.add_argument("--env-file", help="read credentials from this file instead")
    parser.add_argument("--scope", help="report on this pipeline_scope (default: the only one)")
    parser.add_argument("--json", dest="json_out", help="write the full report here")
    parser.add_argument("--no-graph", action="store_true", help="structured arm only")
    parser.add_argument("--no-store", action="store_true", help="graph arm only")
    args = parser.parse_args()

    report = build(args.instance, args.env_file, args.scope,
                   skip_graph=args.no_graph, skip_store=args.no_store)
    print(render(report))
    if args.json_out:
        Path(args.json_out).expanduser().write_text(json.dumps(report, indent=2, default=str))
        print(f"wrote {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())
