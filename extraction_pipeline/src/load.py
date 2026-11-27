"""Step 8 - Load: write Claims and their provenance into Neo4j.

Input : data/output/claims.jsonl (one line per occurrence, step 7b), data/output/sentences.jsonl,
        config/risk_taxonomy/ (groups.csv, themes.csv), data/interim/clean/*.jsonl,
        data/output/subsidiaries.jsonl (the subsidiaries step; optional),
        data/entity_catalog.json (step 1c; optional: the segments)
Output: nodes and relationships in Neo4j, or data/output/load_preview.cypher with --dry-run

Each Claim node is one statement at one place (claim_id = the unit's position id, #45), so
the same sentence twice in a section is two Claim nodes. Rows that share a claim_id (only
in files built with the old text-keyed claim_id) still become one node; the SUPPORTS
relationship then keeps every occurrence's source spans. Claudia's candidate_occurrences.jsonl has
the same format, so her results load by copying that file to data/output/claims.jsonl.

Risk themes: every RiskTheme (32, keyed by theme_id) and RiskGroup (7, keyed by group_id) of
config/risk_taxonomy/ is loaded, each theme with one IN_RISK_GROUP edge. A Claim gets one
HAS_RISK_THEME edge per theme in its risk_themes (step 7b): {rank: primary | secondary,
scope: risk_factor | sentence, source, agrees_with_heading (primary only), model,
taxonomy_version}. There is no node per risk factor: risk_factor_id (the lead's unit_id),
risk_category_text, risk_category_unit_id, heading_risk_group and risk_theme_status
("unknown" when the model found no theme) are Claim properties.

Graph (names from the ontology):
  (Company)-[:FILES]->(Filing)-[:CONTAINS]->(Section)-[:SUPPORTS]->(Claim)-[:HAS_RISK_THEME]->(RiskTheme)
  (RiskTheme)-[:IN_RISK_GROUP]->(RiskGroup)
  (Company)-[:OWNS]->(Subsidiary)
  (Company)-[:HAS_SEGMENT]->(Segment)
  (Claim)-[:MENTIONS]->(Subsidiary | Segment)

Segment nodes come from the XBRL segment members in data/entity_catalog.json (step 1c), keyed
by segment_id (uuid5 of cik + normalised name). Like OWNS, HAS_SEGMENT is one edge per filing
whose facts report the segment, keyed by accession_number, so a reorganisation shows by year.

MENTIONS (#91 P3) is one edge per Claim and entity it links to: the Claim's related_subsidiary_ids
and related_segment_ids (step 7b keeps only matched / matched_auto links there). The edge
carries what was matched: mention_text, match_status, found_by (dictionary, model or both),
entity_annotation_version. A link whose Subsidiary or Segment node is not loaded is skipped by
the MATCH, so load the subsidiaries and the catalog with the Claims.

Subsidiary nodes are keyed by sub_id (parent CIK + normalised name), so one subsidiary listed
in several 10-Ks is one node; each also stores entity_key (legal form removed), for spotting
renames across years. Each 10-K that lists a subsidiary gives one OWNS edge, keyed by the
10-K's accession_number and the edge's two ends (Company, Subsidiary); row_order is kept on
the edge but is not part of the key. The edge carries what that exhibit says (name as filed,
jurisdiction, nesting) and the filing's reporting_period_end, the name the Filing node uses.
OWNS has no section_id: Exhibit 21 is a separate document of the filing, not a section of the
Data team's export. It cites its source by accession_number + source_url (the Exhibit 21
document on EDGAR) + evidence = 'Exhibit 21'.

The parent Company is matched by CIK. Loading subsidiaries never sets the Company's name: a
Company first created by the subsidiaries carries cik and ticker only, and gets its name when
the Claims load (company_name from the Data team's export). Each run loads the claims, the
subsidiaries, or both, whichever files exist.

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

from .common import Paths, filings, load_env, log, read_jsonl, settings
from . import risk_taxonomy
from .subsidiaries import read_rows as subsidiary_rows

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
    "risk_groups": """
UNWIND $rows AS r
MERGE (g:RiskGroup {group_id: r.group_id, pipeline_scope: $scope})
SET g.definition = r.definition, g.taxonomy_version = r.taxonomy_version""",
    "risk_themes": """
UNWIND $rows AS r
MERGE (k:RiskTheme {theme_id: r.theme_id, pipeline_scope: $scope})
SET k.group_id = r.group_id, k.definition = r.definition, k.ey_2016_category = r.ey_2016_category,
    k.taxonomy_version = r.taxonomy_version
WITH k, r
MATCH (g:RiskGroup {group_id: r.group_id, pipeline_scope: $scope})
MERGE (k)-[x:IN_RISK_GROUP {pipeline_scope: $scope}]->(g)""",
    "claims": """
UNWIND $rows AS r
MERGE (c:Claim {claim_id: r.claim_id, pipeline_scope: $scope})
SET c.quote = r.quote, c.claim_type = r.claim_type, c.accession_number = r.accession_number,
    c.section_id = r.section_id, c.extraction_status = r.extraction_status, c.context = r.context,
    c.subject_scope = r.subject_scope, c.annotation_note = r.annotation_note,
    c.is_lead_sentence = r.is_lead_sentence, c.risk_summary = r.risk_summary,
    c.multi_fragment = r.multi_fragment, c.risk_factor_id = r.risk_factor_id,
    c.risk_category_text = r.risk_category_text, c.risk_category_unit_id = r.risk_category_unit_id,
    c.heading_risk_group = r.heading_risk_group, c.risk_theme_status = r.risk_theme_status
WITH c, r
MATCH (s:Section {section_id: r.section_id, pipeline_scope: $scope})
MERGE (s)-[x:SUPPORTS {pipeline_scope: $scope}]->(c)
SET x += r.provenance
WITH c, r
UNWIND r.risk_themes AS t
MATCH (k:RiskTheme {theme_id: t.theme_id, pipeline_scope: $scope})
MERGE (c)-[y:HAS_RISK_THEME {pipeline_scope: $scope}]->(k)
SET y.rank = t.rank, y.scope = t.scope, y.source = t.source, y.agrees_with_heading = t.agrees_with_heading,
    y.model = t.model, y.taxonomy_version = t.taxonomy_version, y.extraction_status = 'needs_review'""",
    "subsidiaries": """
UNWIND $rows AS r
MERGE (s:Subsidiary {sub_id: r.sub_id, pipeline_scope: $scope})
SET s.name = r.name, s.normalised_name = r.normalised_name, s.entity_key = r.entity_key,
    s.parent_cik = r.parent_cik, s.jurisdiction_raw = r.jurisdiction_raw, s.country = r.country,
    s.state_or_region = r.state_or_region, s.extraction_status = 'needs_review'
WITH s, r
MERGE (c:Company {cik: r.parent_cik, pipeline_scope: $scope})
SET c.ticker = r.parent_ticker
MERGE (c)-[o:OWNS {accession_number: r.accession_number, pipeline_scope: $scope}]->(s)
SET o.reporting_period_end = r.reporting_period_end, o.name_as_filed = r.name,
    o.jurisdiction_raw = r.jurisdiction_raw, o.row_order = r.row_order, o.country = r.country,
    o.state_or_region = r.state_or_region, o.ownership_pct = r.ownership_pct, o.level = r.level,
    o.immediate_parent = r.immediate_parent, o.relationship = r.relationship,
    o.parse_method = r.parse_method, o.flags = r.flags, o.source_url = r.source_url,
    o.source_file = r.source_file, o.parser_version = r.parser_version,
    o.evidence = 'Exhibit 21', o.extraction_status = 'needs_review'""",
    "segments": """
UNWIND $rows AS r
MERGE (g:Segment {segment_id: r.segment_id, pipeline_scope: $scope})
SET g.name = r.name, g.aliases = r.aliases, g.cik = r.cik, g.xbrl_members = r.members,
    g.extraction_status = 'needs_review'
WITH g, r
MERGE (c:Company {cik: r.cik, pipeline_scope: $scope})
MERGE (c)-[h:HAS_SEGMENT {accession_number: r.accession_number, pipeline_scope: $scope}]->(g)
SET h.evidence = 'XBRL StatementBusinessSegmentsAxis', h.xbrl_members = r.members,
    h.extraction_status = 'needs_review'""",
    "mentions_subsidiary": """
UNWIND $rows AS r
MATCH (c:Claim {claim_id: r.claim_id, pipeline_scope: $scope})
MATCH (s:Subsidiary {sub_id: r.entity_id, pipeline_scope: $scope})
MERGE (c)-[m:MENTIONS {pipeline_scope: $scope}]->(s)
SET m += r.props""",
    "mentions_segment": """
UNWIND $rows AS r
MATCH (c:Claim {claim_id: r.claim_id, pipeline_scope: $scope})
MATCH (g:Segment {segment_id: r.entity_id, pipeline_scope: $scope})
MERGE (c)-[m:MENTIONS {pipeline_scope: $scope}]->(g)
SET m += r.props""",
}

COUNT_QUERY = """
MATCH (n {pipeline_scope: $scope})
RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label"""

MENTIONS_COUNT = "MATCH (:Claim {pipeline_scope: $scope})-[m:MENTIONS]->() RETURN count(m) AS n"

WIPE_QUERY = "MATCH (n {pipeline_scope: $scope}) DETACH DELETE n"


def merge_occurrences(occurrences: list[dict], roles: dict[str, str]) -> list[dict]:
    """One row per claim_id; a repeated claim_id (old text-keyed files) adds its spans.

    roles maps unit_id (= claim_id) to the unit's role in step 4; old files fall back to "body".
    """
    claims: dict[str, dict] = {}
    for occ in occurrences:
        p = occ["provenance"]
        if occ["claim_id"] in claims:
            claim = claims[occ["claim_id"]]
            prov = claim["provenance"]
            prov["raw_spans"].append(p["raw_spans"])
            claim["multi_fragment"] = claim["multi_fragment"] or bool(occ.get("multi_fragment"))
            have = {t["theme_id"] for t in claim["risk_themes"]}
            claim["risk_themes"] += [t for t in occ.get("risk_themes") or [] if t["theme_id"] not in have]
            claim["is_lead_sentence"] = claim["is_lead_sentence"] or bool(occ.get("is_lead_sentence"))
            claim["risk_summary"] = claim["risk_summary"] and bool(occ.get("risk_summary"))
            continue
        claims[occ["claim_id"]] = {
            **{k: occ.get(k) for k in ("claim_id", "quote", "claim_type", "accession_number", "section_id",
                                       "extraction_status", "context", "subject_scope", "annotation_note")},
            **{k: occ.get(k) for k in ("risk_factor_id", "risk_category_text", "risk_category_unit_id",
                                       "risk_theme_status")},
            "heading_risk_group": list(occ.get("heading_risk_group") or []),
            "risk_themes": [dict(t) for t in occ.get("risk_themes") or []],
            "is_lead_sentence": bool(occ.get("is_lead_sentence")),
            "risk_summary": bool(occ.get("risk_summary")),
            "multi_fragment": bool(occ.get("multi_fragment")),
            "provenance": {
                "section_key": p["section_key"],
                "section_path": p["section_path"],
                "lead_unit_id": p.get("lead_unit_id"),
                "lead_source": p.get("lead_source"),
                "role": roles.get(occ["claim_id"], "body"),
                "context_unit_ids": p.get("context_unit_ids") or [],
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
        "section_key": p["section_key"],
        "section_path": p["section_path"],
        "lead_unit_id": p["lead_unit_id"],
        "lead_source": p["lead_source"],
        "role": p["role"],
        "context_unit_ids": p["context_unit_ids"],
        "raw_spans_json": json.dumps(p["raw_spans"]),
        "xpath": (p["source_locator"] or {}).get("xpath"),
        "source_file": (p["source_locator"] or {}).get("source_file"),
        "processing_set_id": p["processing_set_id"],
        "model": p["model"],
        "prompt_version": p["prompt_version"],
    }


def segment_rows() -> list[dict]:
    """One row per segment and filing that reports it (HAS_SEGMENT is keyed by accession)."""
    path = Paths.entity_catalog()
    if not path.exists():
        return []
    rows = []
    for e in json.loads(path.read_text(encoding="utf-8")):
        if e["entity_type"] != "segment":
            continue
        for accession in e["accessions"]:
            rows.append({"segment_id": e["entity_id"], "name": e["name"], "aliases": e["aliases"],
                         "members": e.get("members") or [], "cik": e["issuer_cik"],
                         "accession_number": accession})
    return rows


def mention_rows(occurrences: list[dict]) -> dict[str, list[dict]]:
    """MENTIONS edges by target label: one per (claim_id, entity_id) in related_*_ids."""
    out: dict[str, dict[tuple, dict]] = {"subsidiary": {}, "segment": {}}
    for occ in occurrences:
        for kind in ("subsidiary", "segment"):
            for entity_id in occ.get(f"related_{kind}_ids") or []:
                key = (occ["claim_id"], entity_id)
                if key in out[kind]:
                    continue
                m = next((m for m in occ.get("entity_mentions") or []
                          if entity_id in (m.get("candidate_entity_ids") or [])), {})
                out[kind][key] = {"claim_id": occ["claim_id"], "entity_id": entity_id, "props": {
                    "mention_text": m.get("mention_text"), "match_status": m.get("match_status"),
                    "found_by": m.get("found_by") or [m.get("origin") or "model"],
                    "entity_annotation_version": occ.get("entity_annotation_version"),
                    "extraction_status": "needs_review"}}
    return {k: list(v.values()) for k, v in out.items()}


def build_rows() -> dict[str, list[dict]]:
    roles = {u["unit_id"]: u["role"] for u in read_jsonl(Paths.sentences())} \
        if Paths.sentences().exists() else {}
    subsidiaries = subsidiary_rows()
    if not Paths.claims().exists() and not subsidiaries:
        raise SystemExit(f"Nothing to load: neither {Paths.claims()} nor the subsidiaries output exists.")
    occurrences = list(read_jsonl(Paths.claims())) if Paths.claims().exists() else []
    if not occurrences:
        log(STEP, f"no Claims to load ({Paths.claims()} missing or empty); loading subsidiaries only")
    claims = merge_occurrences(occurrences, roles)
    used_sections = {c["section_id"] for c in claims}
    sections, filing_rows, companies = {}, {}, {}
    for f in filings() if claims else []:
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

    version = risk_taxonomy.version() if claims else None
    groups = [{"group_id": g, "definition": row["definition"], "taxonomy_version": version}
              for g, row in risk_taxonomy.groups().items()] if claims else []
    themes = [{"theme_id": t, "group_id": row["group_id"], "definition": row["definition"],
               "ey_2016_category": row.get("ey_2016_category"), "taxonomy_version": version}
              for t, row in risk_taxonomy.themes().items()] if claims else []

    claim_rows = [{**{k: v for k, v in c.items() if k != "provenance"},
                   "provenance": flat_provenance(c["provenance"])} for c in claims]
    mentions = mention_rows(occurrences)
    return {"companies": list(companies.values()), "filings": list(filing_rows.values()),
            "sections": list(sections.values()), "risk_groups": groups, "risk_themes": themes,
            "claims": claim_rows,
            "subsidiaries": subsidiaries, "segments": segment_rows(),
            "mentions_subsidiary": mentions["subsidiary"], "mentions_segment": mentions["segment"]}


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
        n = session.run(MENTIONS_COUNT, scope=scope).single()["n"]
        expected = len(rows["mentions_subsidiary"]) + len(rows["mentions_segment"])
        log(STEP, f"MENTIONS edges: {n}" + (f" (of {expected} links; the rest point at a Subsidiary "
                                            "or Segment that is not loaded)" if n < expected else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="write the queries to a file, connect to nothing")
    parser.add_argument("--wipe", action="store_true", help="delete all nodes with this pipeline_scope")
    args = parser.parse_args()
    run(dry_run=args.dry_run, wipe=args.wipe)


if __name__ == "__main__":
    main()
