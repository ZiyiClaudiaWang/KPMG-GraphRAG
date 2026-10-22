"""Check baseline and source revision records offline, without credentials."""

import csv
import importlib.util
import json
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    multi = here.parent
    spec = importlib.util.spec_from_file_location(
        "saved_baseline_checker", multi / "validate_saved_results.py"
    )
    baseline_checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline_checker)
    baseline_checker.main()
    require = baseline_checker.require
    stable_id = baseline_checker.stable_id
    evidence_text = baseline_checker.evidence_text

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    added_id = "claim_db07a5c3b920fe4e21842040"
    context_ids = {
        "claim_d8d8d592118884cb6fe0747b",
        "claim_dfa29cb3c5a3b246d89bb9d4",
    }
    allowed_changed_fields = {
        "context", "context_evidence_fragments", "annotation_note", "quality_revision_status"
    }
    all_records = {}
    sections = {}
    changed = set()
    original_ids = set()
    for ticker, expected_count in {"AAPL": 10, "MSFT": 15, "JPM": 14}.items():
        section = read(multi / "results" / ticker / "source_excerpt.json")
        sections[section["accession_number"]] = section
        blocks = {b["block_order"]: b for b in section["blocks"]}
        positions = {b["block_order"]: i for i, b in enumerate(section["blocks"])}
        baseline = {
            r["claim_id"]: r for r in read(multi / "results" / ticker / "claims.json")
        }
        original_ids.update(baseline)
        records = read(here / "results" / f"{ticker}_claims.json")
        require(len(records) == expected_count, "Unexpected revised Claim count.")
        for record in records:
            cid = record["claim_id"]
            require(cid not in all_records, "Duplicate revised Claim ID.")
            all_records[cid] = record
            require(record["extraction_status"] == "needs_review"
                    and record["source_chunk_id"] is None, "Review or chunk state differs.")
            require(record["claim_type"] in {"stated_fact", "forward_looking", "hedged_risk"},
                    "Unexpected Claim type.")
            require(evidence_text(record["evidence_fragments"], blocks, positions)
                    == record["quote"], "Revised quote differs from source evidence.")
            if record["context"] is None:
                require(not record["context_evidence_fragments"], "Null context has evidence.")
            else:
                require(evidence_text(record["context_evidence_fragments"], blocks, positions)
                        == record["context"], "Revised context differs from source evidence.")
            require(record["cross_block"] == (len(record["evidence_fragments"]) > 1),
                    "Revised cross-block flag differs.")
            require(record["section_id"] == f"{section['accession_number']}/{section['section_key']}"
                    and record["accession_number"] == section["accession_number"]
                    and record["issuer_cik"] == section["cik"]
                    and record["source_url"] == section["source_url"]
                    and record["source_document_sha256"] == section["sha256"]
                    and record["section_identity_status"] == section["identity_status"],
                    "Revised Claim source provenance differs.")
            require(cid == stable_id("claim", section["accession_number"],
                    section["section_key"], " ".join(record["quote"].split())),
                    "Revised Claim deterministic identity differs.")
            if cid in baseline:
                old = baseline[cid]
                differences = {key for key in set(record) | set(old)
                               if record.get(key) != old.get(key)}
                if differences:
                    changed.add(cid)
                    require(cid in context_ids and differences <= allowed_changed_fields,
                            "Unlogged Claim content changed outside the context revision.")
                    require(record.get("quality_revision_status") == "needs_review",
                            "Revision was represented as reviewed.")
            else:
                require(cid == added_id and ticker == "AAPL", "Unexpected added Claim.")
                legacy = next(r for r in read(multi.parent / "results" / "claims.json")
                              if r["claim_id"] == cid)
                for field in ("quote", "claim_type", "subject_scope", "extraction_model",
                              "extraction_request_hash", "source_document_sha256"):
                    require(record[field] == legacy[field], "Recovery provenance differs.")
                require(record["review_flags"] == ["recovered_from_prior_pilot"]
                        and record.get("quality_revision_status") == "needs_review",
                        "Recovery status differs.")

    require(set(all_records) - original_ids == {added_id}
            and original_ids <= set(all_records) and changed == context_ids,
            "Revision is not exactly one recovery and two context updates.")
    changes = read(here / "results" / "changes.json")
    require(len(changes) == 3 and {c["claim_id"] for c in changes} == context_ids | {added_id},
            "Change log does not cover the exact revision.")
    for change in changes:
        cid = change["claim_id"]
        if cid == added_id:
            require(change["operation"] == "recover_prior_extraction"
                    and change["prior_request_hash"] == all_records[cid]["extraction_request_hash"],
                    "Recovery change log differs.")
        else:
            old = next(r for r in read(multi / "results/JPM/claims.json") if r["claim_id"] == cid)
            require(change["operation"] == "add_source_context"
                    and change["previous_context"] == old["context"]
                    and change["revised_context"] == all_records[cid]["context"]
                    and change["evidence_fragments"] == all_records[cid]["context_evidence_fragments"],
                    "Context change log differs.")
    situation = all_records["claim_d8d8d592118884cb6fe0747b"]
    require(len(situation["context_evidence_fragments"]) == 1
            and situation["context_evidence_fragments"][0]["block_order"] == 41,
            "Situation context does not point to source block 41.")
    jpm_blocks = {b["block_order"]: b["text"]
                  for b in sections[situation["accession_number"]]["blocks"]}
    require(situation["context"] == jpm_blocks[41].split(situation["quote"], 1)[0].strip(),
            "Situation context is not the exact preceding source text.")
    list_claim = all_records["claim_dfa29cb3c5a3b246d89bb9d4"]
    require([f["block_order"] for f in list_claim["context_evidence_fragments"]]
            == list(range(26, 35)), "List context omits source blocks.")
    require(list_claim["context"] == " ".join(jpm_blocks[i] for i in range(26, 35)),
            "List context does not preserve the complete introduction and list.")

    verification = read(here / "results/verification.json")
    require(verification["prototype_id"] == "cw-extraction-three-filings-revision1"
            and verification["counts"] == {"nodes": 48, "relationships": 45}
            and verification["path_count"] == 39, "Unexpected revision graph record.")
    for key in ("source_alignment", "stored_claims_and_context", "repeated_loading",
                "simulated_chunk_metadata_changes", "simulation_cleanup"):
        require(verification[key] == "passed", f"Recorded revision check failed: {key}")
    require(verification["classification_review_status"] == "needs_review"
            and verification["extraction_completeness"] == "not_established"
            and verification["actual_data_team_rechunking"] == "not_tested",
            "Revision validation limits were changed.")
    paths = read(here / "results/verified_paths.json")
    require(len(paths) == len(all_records) == 39
            and {p["claim_id"] for p in paths} == set(all_records), "Revision path coverage differs.")
    for path in paths:
        claim = all_records[path["claim_id"]]
        section = sections[claim["accession_number"]]
        require(all(path[k] == claim[k] for k in ("quote", "context", "cross_block", "issuer_cik")),
                "Revision path content differs.")
        hops = path["hops"]
        require([h["type"] for h in hops] == ["FILES", "CONTAINS", "SUPPORTS"],
                "Revision path structure differs.")
        identities = [
            stable_id("edge", section["cik"], "FILES", section["accession_number"], claim["section_id"]),
            stable_id("edge", section["accession_number"], "CONTAINS", claim["section_id"], claim["section_id"]),
            stable_id("edge", claim["section_id"], "SUPPORTS", claim["claim_id"], claim["section_id"]),
        ]
        for hop, identity in zip(hops, identities):
            require(hop["edge_id"] == identity
                    and hop["prototype_id"] == verification["prototype_id"], "Revision edge identity differs.")
            require(all(hop[k] == claim[k] for k in (
                "accession_number", "section_id", "source_url", "source_document_sha256"
            )), "Revision hop provenance differs.")
        require(json.loads(hops[1]["source_locator_json"])["xpath"] == section["start_heading_xpath"],
                "Revision heading locator differs.")
        for field in ("evidence_fragments", "context_evidence_fragments"):
            require(json.loads(hops[-1][f"{field}_json"]) == claim[field],
                    "Revision supporting evidence differs.")
    with (here / "results/review_queue.csv").open(encoding="utf-8", newline="") as f:
        queue = list(csv.DictReader(f))
    require(len(queue) == 39 and {r["claim_id"] for r in queue} == set(all_records),
            "Revision review queue coverage differs.")
    for row in queue:
        claim = all_records[row["claim_id"]]
        for field in ("quote", "context", "claim_type", "annotation_note", "extraction_status"):
            require(row[field] == (claim[field] or ""), "Revision review queue content differs.")
        require(json.loads(row["review_flags"]) == claim["review_flags"], "Review flags differ.")
    print("Revision checks passed: exactly one recovered Claim and two context updates.")
    print("Verified saved revision: 39 paths, 48 nodes, 45 relationships.")
    print("Review and full recall checks remain open; no external services were called.")


if __name__ == "__main__":
    main()
