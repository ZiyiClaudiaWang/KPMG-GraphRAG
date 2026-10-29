"""Step 8 - Load: write Claims and their provenance into Neo4j.

Input : data/output/claims.jsonl (one line per occurrence, step 7b), data/output/sentences.jsonl,
        data/output/risk_factors.csv, data/interim/clean/*.jsonl
Output: nodes and relationships in Neo4j, or data/output/load_preview.cypher with --dry-run

Occurrences with the same claim_id become one Claim node; the SUPPORTS relationship keeps
every occurrence's sentence id and source spans. Claudia's candidate_occurrences.jsonl has
the same format, so her results load by copying that file to data/output/claims.jsonl.

Graph (names from the ontology):
  (Company)-[:FILES]->(Filing)-[:CONTAINS]->(Section)-[:SUPPORTS]->(Claim)-[:CONCERNS_RISK]->(RiskFactor)

Every node and relationship carries pipeline_scope (config/settings.yaml), and
nodes are merged on their key plus that scope. The pilot therefore never
touches nodes from other work in the same database, and can be removed with
--wipe.

Neo4j credentials come from extraction_pipeline/.env:
  NEO4J_URI=neo4j+s://<id>.databases.neo4j.io
  NEO4J_USERNAME=neo4j
  NEO4J_PASSWORD=...
  NEO4J_DATABASE=neo4j        (optional)

Usage:
  python -m src.load --dry-run     write the queries and row counts, connect to nothing
  python -m src.load               load (safe to repeat: everything is MERGEd)
  python -m src.load --wipe        delete everything with this pipeline_scope
"""

from __future__ import annotations

import argparse
import json
import os

from .common import Paths, filings, load_env, log, read_csv, read_jsonl, settings

STEP = "load"
BATCH = 500

QUERIES = {
    "companies": """
UNWIND $rows AS r
MERGE (c:Company {cik: r.cik, pipeline_scope: $scope})
SET c.name = r.name, c.ticker = r.ticker""",
    "filings": """
UNWIND $rows AS r
MERGE (f:Filing {accession_number: r.accession_number, pipeline_scope: $scope})
SET f.form = r.form, f.filing_date = r.filing_date, f.reporting_period_end = r.reporting_period_end,
    f.cik = r.cik
WITH f, r
MATCH (c:Company {cik: r.cik, pipeline_scope: $scope})
MERGE (c)-[x:FILES {pipeline_scope: $scope}]->(f)""",
    "sections": """
UNWIND $rows AS r
MERGE (s:Section {section_id: r.section_id, pipeline_scope: $scope})
SET s.section_key = r.section_key, s.section_type = r.section_type, s.title = r.title,
    s.part = r.part, s.item = r.item, s.accession_number = r.accession_number,
    s.xpath = r.xpath, s.processing_set_id = r.processing_set_id
WITH s, r
MATCH (f:Filing {accession_number: r.accession_number, pipeline_scope: $scope})
MERGE (f)-[x:CONTAINS {pipeline_scope: $scope}]->(s)""",
    "risk_factors": """
UNWIND $rows AS r
MERGE (k:RiskFactor {risk_factor_id: r.risk_factor_id, pipeline_scope: $scope})
SET k.name = r.name, k.ticker = r.ticker, k.match_method = r.match_method,
    k.extraction_status = 'needs_review'""",
    "claims": """
UNWIND $rows AS r
MERGE (c:Claim {claim_id: r.claim_id, pipeline_scope: $scope})
SET c.quote = r.quote, c.claim_type = r.claim_type, c.accession_number = r.accession_number,
    c.section_id = r.section_id, c.extraction_status = r.extraction_status, c.context = r.context,
    c.subject_scope = r.subject_scope, c.annotation_note = r.annotation_note
WITH c, r
MATCH (s:Section {section_id: r.section_id, pipeline_scope: $scope})
MERGE (s)-[x:SUPPORTS {pipeline_scope: $scope}]->(c)
SET x += r.provenance
WITH c, r
WHERE r.risk_factor_id IS NOT NULL
MATCH (k:RiskFactor {risk_factor_id: r.risk_factor_id, pipeline_scope: $scope})
MERGE (c)-[y:CONCERNS_RISK {pipeline_scope: $scope}]->(k)""",
}

COUNT_QUERY = """
MATCH (n {pipeline_scope: $scope})
RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label"""

WIPE_QUERY = "MATCH (n {pipeline_scope: $scope}) DETACH DELETE n"


def merge_occurrences(occurrences: list[dict], roles: dict[str, str]) -> list[dict]:
    """One row per claim_id; repeated occurrences add their sentence ids and spans."""
    claims: dict[str, dict] = {}
    for occ in occurrences:
        p = occ["provenance"]
        if occ["claim_id"] in claims:
            prov = claims[occ["claim_id"]]["provenance"]
            prov["sentence_ids"] += p["sentence_ids"]
            prov["raw_spans"].append(p["raw_spans"])
            continue
        claims[occ["claim_id"]] = {
            **{k: occ.get(k) for k in ("claim_id", "quote", "claim_type", "accession_number", "section_id",
                                       "extraction_status", "context", "subject_scope", "annotation_note",
                                       "risk_factor_id")},
            "provenance": {
                "sentence_ids": list(p["sentence_ids"]),
                "section_key": p["section_key"],
                "section_path": p["section_path"],
                "risk_heading_id": None,
                "role": roles.get(p["sentence_ids"][0], "body"),
                "context_sentence_ids": p.get("context_sentence_ids") or [],
                "raw_spans": [p["raw_spans"]],
                "source_locator": p.get("source_locator"),
                "processing_set_id": p.get("processing_set_id"),
                "model": p.get("model"),
                "prompt_version": p.get("prompt_version"),
            },
        }
    return list(claims.values())


def flat_provenance(p: dict) -> dict:
    """Neo4j properties must be primitives or lists of them; nested values become JSON strings."""
    return {
        "sentence_ids": p["sentence_ids"],
        "section_key": p["section_key"],
        "section_path": p["section_path"],
        "risk_heading_id": p["risk_heading_id"],
        "role": p["role"],
        "context_sentence_ids": p["context_sentence_ids"],
        "raw_spans_json": json.dumps(p["raw_spans"]),
        "xpath": (p["source_locator"] or {}).get("xpath"),
        "source_file": (p["source_locator"] or {}).get("source_file"),
        "processing_set_id": p["processing_set_id"],
        "model": p["model"],
        "prompt_version": p["prompt_version"],
    }


def build_rows() -> dict[str, list[dict]]:
    roles = {u["sentence_id"]: u["role"] for u in read_jsonl(Paths.sentences())} \
        if Paths.sentences().exists() else {}
    claims = merge_occurrences(list(read_jsonl(Paths.claims())), roles)
    used_sections = {c["section_id"] for c in claims}
    sections, filing_rows, companies = {}, {}, {}
    for f in filings():
        for sec in read_jsonl(Paths.cleaned(f)):
            if sec["section_id"] not in used_sections:
                continue
            sections[sec["section_id"]] = {
                "section_id": sec["section_id"], "section_key": sec["section_key"],
                "section_type": sec["section_type"], "title": sec["original_title"],
                "part": sec["part"], "item": sec["item"], "accession_number": sec["accession_number"],
                "xpath": (sec["source_locator"] or {}).get("xpath"),
                "processing_set_id": sec["processing_set_id"],
            }
            filing_rows[sec["accession_number"]] = {
                "accession_number": sec["accession_number"], "form": sec["form"],
                "filing_date": str(sec["filing_date"]), "reporting_period_end": str(sec["reporting_period_end"]),
                "cik": sec["cik"],
            }
            companies[sec["cik"]] = {"cik": sec["cik"], "name": sec["company_name"], "ticker": sec["ticker"]}

    used_factors = {c["risk_factor_id"] for c in claims if c["risk_factor_id"]}
    factors = [r for r in read_csv(Paths.risk_factors()) if r["risk_factor_id"] in used_factors] \
        if Paths.risk_factors().exists() else []

    claim_rows = [{**{k: v for k, v in c.items() if k != "provenance"},
                   "provenance": flat_provenance(c["provenance"])} for c in claims]
    return {"companies": list(companies.values()), "filings": list(filing_rows.values()),
            "sections": list(sections.values()), "risk_factors": factors, "claims": claim_rows}


def write_preview(rows: dict[str, list[dict]], scope: str) -> None:
    lines = [f"// pipeline_scope = {scope}"]
    for name, query in QUERIES.items():
        sample = json.dumps(rows[name][:1], ensure_ascii=False, indent=1)
        lines += [f"\n// {name}: {len(rows[name])} rows; first row:", "// " + sample.replace("\n", "\n// "),
                  query.strip() + ";"]
    Paths.cypher().parent.mkdir(parents=True, exist_ok=True)
    Paths.cypher().write_text("\n".join(lines) + "\n", encoding="utf-8")


def connect():
    load_env()
    from neo4j import GraphDatabase  # imported here so other steps run without the package
    missing = [k for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in extraction_pipeline/.env")
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
    driver.verify_connectivity()
    return driver, os.environ.get("NEO4J_DATABASE") or None


def run(dry_run: bool = False, wipe: bool = False) -> None:
    scope = settings()["load"]["scope"]
    if wipe:
        driver, database = connect()
        with driver, driver.session(database=database) as session:
            summary = session.run(WIPE_QUERY, scope=scope).consume()
            log(STEP, f"deleted {summary.counters.nodes_deleted} nodes with pipeline_scope={scope}")
        return

    rows = build_rows()
    counts = ", ".join(f"{k}={len(v)}" for k, v in rows.items())
    if dry_run:
        write_preview(rows, scope)
        log(STEP, f"dry run: {counts} -> {Paths.cypher()}")
        return

    driver, database = connect()
    with driver, driver.session(database=database) as session:
        for name, query in QUERIES.items():       # order matters: parents before children
            data = rows[name]
            for i in range(0, len(data), BATCH):
                session.run(query, rows=data[i:i + BATCH], scope=scope).consume()
            log(STEP, f"{name}: {len(data)} rows merged")
        result = session.run(COUNT_QUERY, scope=scope)
        log(STEP, "in the graph: " + ", ".join(f"{r['label']}={r['n']}" for r in result))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="write the queries to a file, connect to nothing")
    parser.add_argument("--wipe", action="store_true", help="delete all nodes with this pipeline_scope")
    args = parser.parse_args()
    run(dry_run=args.dry_run, wipe=args.wipe)


if __name__ == "__main__":
    main()
