"""Validate the saved 87-Claim graph evidence without contacting Neo4j or an API."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

from prepare_gap_candidates import prepare, read, stable_id


def validate(folder=None):
    folder = Path(folder) if folder else Path(__file__).resolve().parent
    results = folder / "results"
    with tempfile.TemporaryDirectory() as temporary:
        summary = prepare(folder / "checkpoint", temporary)
        for name in [
            "candidate_claims_by_ticker.json", "summary.json", "deferred_drafts.json",
            "boundary_review_groups.json", "gap_assessments.json", "mention_candidates.json",
            "duplicate_candidates.json",
        ]:
            if read(Path(temporary) / name) != read(results / name):
                raise ValueError("Prepared result differs: " + name)

    candidates = read(results / "candidate_claims_by_ticker.json")
    expected = {r["claim_id"]: r for rows in candidates.values() for r in rows}
    if len(expected) != summary["total_candidate_claims"]:
        raise ValueError("Claim identities overlap across filings.")
    sections = {t: read(folder / "checkpoint" / t / "item1a.json") for t in candidates}
    sections_by_accession = {s["accession_number"]: s for s in sections.values()}
    paths = read(results / "verified_paths.json")
    if len(paths) != len(expected) or {p["claim_id"] for p in paths} != set(expected):
        raise ValueError("Expected one saved path per Claim.")
    scope = "cw-extraction-three-filings-gap-v1"
    unique_edges = {}
    for path in paths:
        record = expected[path["claim_id"]]
        section = sections_by_accession[record["accession_number"]]
        for field in ["quote", "context", "issuer_cik"]:
            if path[field] != record[field]:
                raise ValueError("Saved path differs: " + field)
        hops = path["hops"]
        if [h["type"] for h in hops] != ["FILES", "CONTAINS", "SUPPORTS"]:
            raise ValueError("Incorrect path structure.")
        edge_ids = [
            stable_id("edge", record["issuer_cik"], "FILES", record["accession_number"], record["section_id"]),
            stable_id("edge", record["accession_number"], "CONTAINS", record["section_id"], record["section_id"]),
            stable_id("edge", record["section_id"], "SUPPORTS", record["claim_id"], record["section_id"]),
        ]
        for hop, edge_id, kind in zip(hops, edge_ids, ["filing_metadata", "section_heading", "source_sentence"]):
            if hop["edge_id"] != edge_id or hop["prototype_id"] != scope or hop["evidence_kind"] != kind:
                raise ValueError("Incorrect relationship identity or evidence kind.")
            for field in ["accession_number", "section_id", "source_url", "source_document_sha256"]:
                if hop[field] != record[field]:
                    raise ValueError("Incorrect relationship provenance: " + field)
            if edge_id in unique_edges and unique_edges[edge_id] != hop:
                raise ValueError("Shared relationship has inconsistent saved properties.")
            unique_edges[edge_id] = hop
        if json.loads(hops[1]["source_locator_json"]) != {"xpath": section["start_heading_xpath"]}:
            raise ValueError("Incorrect section heading XPath.")
        for field in ["evidence_fragments", "context_evidence_fragments"]:
            if json.loads(hops[-1][field + "_json"]) != record[field]:
                raise ValueError("Incorrect saved relationship evidence: " + field)
        if hops[-1]["span_coordinate_system"] != "normalized_block_text":
            raise ValueError("Incorrect source coordinate system.")
        if hops[-1].get("source_chunk_id") != record.get("source_chunk_id"):
            raise ValueError("Original chunk metadata differs.")

    verification = read(results / "verification.json")
    counts = {"nodes": 3 * len(sections) + len(expected),
              "relationships": 2 * len(sections) + len(expected)}
    required = {
        "prototype_id": scope, "counts": counts,
        "claim_counts": {t: len(rows) for t, rows in candidates.items()},
        "verified_path_count": len(expected), "repeated_loading": "passed",
        "path_and_provenance_verification": "passed",
        "stored_claims_contexts_and_source_locations": "passed",
        "simulated_chunk_metadata_changes": "passed", "original_metadata_restored": "passed",
        "classification_review_status": "needs_review", "extraction_completeness": "not_established",
        "actual_data_team_rechunking": "not_tested",
        "scope": "Selected excerpts from three SEC filings.",
    }
    if any(verification[k] != value for k, value in required.items()):
        raise ValueError("Live verification record differs from expected scope or counts.")
    if len(unique_edges) != counts["relationships"]:
        raise ValueError("Saved unique relationship count differs.")
    if datetime.fromisoformat(verification["verified_at_utc"]).tzinfo is None:
        raise ValueError("Missing verification timezone.")
    runtime = read(results / "runtime.json")
    if any(not isinstance(runtime.get(p), str) or not runtime[p]
           for p in ["neo4j", "anthropic", "pydantic", "lxml"]):
        raise ValueError("Missing recorded runtime package versions.")
    return {"paths": len(paths), **counts, "review_status": "needs_review"}


if __name__ == "__main__":
    print("Saved graph checks passed:", validate())
    print("Offline consistency checks only; live checks were recorded by CW in Colab.")
