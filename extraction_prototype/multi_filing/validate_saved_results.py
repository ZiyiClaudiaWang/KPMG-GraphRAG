"""Validate the saved three-filing prototype without API/database calls."""

import csv
import hashlib
import json
import re
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stable_id(prefix, *parts):
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def evidence_text(fragments, blocks, positions):
    require(bool(fragments), "Missing evidence fragments.")
    orders = [f["block_order"] for f in fragments]
    require(all(order in blocks for order in orders), "Evidence block is absent.")
    indexes = [positions[order] for order in orders]
    require(indexes == list(range(indexes[0], indexes[0] + len(indexes))),
            "Evidence blocks are not consecutive and ordered.")
    for i, fragment in enumerate(fragments):
        block = blocks[fragment["block_order"]]
        text = block["text"]
        start, end = fragment["start"], fragment["end"]
        require(isinstance(start, int) and isinstance(end, int)
                and 0 <= start < end <= len(text), "Invalid fragment span.")
        require(text[start:end] == fragment["quote_fragment"],
                "Evidence span does not reproduce source text.")
        require(fragment["source_locator"] == block["source_locator"],
                "Fragment XPath differs from source block.")
        require(fragment["span_coordinate_system"] == "normalized_block_text",
                "Unexpected span coordinate system.")
        if len(fragments) > 1:
            require(i == 0 or start == 0, "Cross-block evidence skips leading text.")
            require(i == len(fragments) - 1 or end == len(text),
                    "Cross-block evidence skips trailing text.")
    return " ".join(fragment["quote_fragment"] for fragment in fragments)


def main():
    directory = Path(__file__).resolve().parent
    data = directory / "results"

    def read(name):
        return json.loads((data / name).read_text(encoding="utf-8"))

    manifest = read("manifest.json")
    schema = read("extraction_schema_v2.json")
    verification = read("verification.json")
    runtime = read("runtime.json")
    boundaries = read("section_boundaries.json")
    headings = read("heading_candidates.json")
    all_claims = {}
    sections = {}
    resolved_counts = {}
    aliases = {
        "AAPL": {"the company", "company", "apple", "apple inc."},
        "MSFT": {"we", "our", "us", "microsoft", "microsoft corporation"},
        "JPM": {"the firm", "firm", "jpmorganchase", "jpmorgan chase",
                "jpmorgan chase & co."},
    }
    expected_counts = {"AAPL": 9, "MSFT": 15, "JPM": 14}
    require({m["ticker"] for m in manifest} == set(expected_counts),
            "Unexpected filing manifest.")
    for entry in manifest:
        ticker = entry["ticker"]
        metadata = read(f"{ticker}/metadata.json")
        section = read(f"{ticker}/source_excerpt.json")
        run = read(f"{ticker}/model_extraction.json")
        claims = read(f"{ticker}/claims.json")
        mentions = read(f"{ticker}/mention_resolution.json")
        require(entry == metadata, f"{ticker}: metadata differs from manifest.")
        for key, value in metadata.items():
            require(section[key] == value, f"{ticker}: source metadata differs: {key}")
        for bound, field in zip(boundaries[ticker], (
            "start_heading_xpath", "end_heading_xpath_exclusive"
        )):
            require(headings[ticker][bound]["xpath"] == section[field],
                    f"{ticker}: reviewed heading boundary differs.")
        require(section["identity_status"] == "prototype_local_pending_data_alignment",
                "Section identity was represented as authoritative.")
        request = run["request"]
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        require(fingerprint == run["request_hash"], "Request fingerprint differs.")
        require(request["schema"] == schema, "Schema differs from saved request.")
        require(request["model"] == runtime["model"], "Requested model differs.")
        require(request["source_sha256"] == run["source_sha256"] == section["sha256"],
                "Source checksums differ.")
        require(run["accession_number"] == section["accession_number"]
                and run["section_key"] == section["section_key"],
                "Run source identity differs.")
        require(run["stop_reason"] == "end_turn"
                and run["source_alignment_status"] == "passed", "Run was incomplete.")
        require(request["payload"]["blocks"] == [
            {"block_order": b["block_order"], "text": b["text"]}
            for b in section["blocks"]
        ], "Source excerpt differs from model input.")
        blocks = {b["block_order"]: b for b in section["blocks"]}
        positions = {b["block_order"]: i for i, b in enumerate(section["blocks"])}
        require(len(blocks) == len(section["blocks"]), "Duplicate block identity.")
        drafts = run["extraction"]["claims"]
        require(len(claims) == len(drafts) == len(run["evidence_records"])
                == expected_counts[ticker], "Unexpected saved Claim count.")
        for claim, draft, evidence in zip(claims, drafts, run["evidence_records"]):
            require(claim["quote"] == draft["quote"] == evidence["quote"],
                    "Claim quote differs from extraction.")
            require(claim["evidence_fragments"] == evidence["locations"]
                    and claim["context_evidence_fragments"] == evidence["context_locations"],
                    "Resolved evidence differs from extraction audit.")
            for raw, located in zip(draft["evidence_fragments"], claim["evidence_fragments"]):
                require(all(located[k] == v for k, v in raw.items()),
                        "Raw evidence fragment differs from located fragment.")
            require(len(draft["evidence_fragments"]) == len(claim["evidence_fragments"]),
                    "Evidence fragment count differs.")
            require(evidence_text(claim["evidence_fragments"], blocks, positions)
                    == claim["quote"], "Assembled evidence differs from quote.")
            require(claim["context"] == draft["context"], "Context differs from extraction.")
            require(len(draft["context_fragments"])
                    == len(claim["context_evidence_fragments"]), "Context fragment count differs.")
            for raw, located in zip(draft["context_fragments"], claim["context_evidence_fragments"]):
                require(all(located[k] == v for k, v in raw.items()), "Context fragment differs.")
            if claim["context"] is None:
                require(not claim["context_evidence_fragments"], "Null context has evidence.")
            else:
                require(evidence_text(claim["context_evidence_fragments"], blocks, positions)
                        == claim["context"], "Context evidence differs from text.")
            require(claim["claim_type"] == draft["claim_type"]
                    and claim["claim_type"] in {"stated_fact", "forward_looking", "hedged_risk"},
                    "Claim classification differs.")
            require(claim["subject_scope"] == draft["subject_text"]
                    and claim["annotation_note"] == draft["annotation_note"],
                    "Claim subject or annotation differs.")
            require(claim["extraction_status"] == "needs_review"
                    and claim["source_chunk_id"] is None, "Review or chunk state differs.")
            require(claim["cross_block"] == (len(claim["evidence_fragments"]) > 1),
                    "Cross-block flag differs.")
            section_id = f"{section['accession_number']}/{section['section_key']}"
            require(claim["section_id"] == section_id
                    and claim["accession_number"] == section["accession_number"]
                    and claim["issuer_cik"] == section["cik"]
                    and claim["source_url"] == section["source_url"]
                    and claim["source_document_sha256"] == section["sha256"],
                    "Claim source identity differs.")
            require(claim["extraction_model"] == run["response_model"]
                    and claim["extraction_request_hash"] == fingerprint,
                    "Claim extraction provenance differs.")
            require(claim["claim_id"] == stable_id(
                "claim", section["accession_number"], section["section_key"],
                " ".join(claim["quote"].split())
            ), "Claim ID differs from deterministic rule.")
            require(claim["claim_id"] not in all_claims, "Duplicate Claim identity.")
            all_claims[claim["claim_id"]] = claim
        draft_mentions = run["extraction"]["mentions"]
        require(len(mentions) == len(draft_mentions) == 5, "Unexpected mention count.")
        for mention, draft in zip(mentions, draft_mentions):
            require(all(mention[k] == v for k, v in draft.items()), "Mention extraction differs.")
            block = blocks[mention["block_order"]]
            expected_spans = [{"start": m.start(), "end": m.end()} for m in re.finditer(
                re.escape(mention["surface_text"]), block["text"]
            )]
            require(bool(mention["surface_text"].strip()) and bool(expected_spans)
                    and mention["source_spans"] == expected_spans,
                    "Mention source spans differ.")
            require(mention["source_locator"] == block["source_locator"], "Mention XPath differs.")
            normalized = re.sub(r"['’]s$", "", mention["surface_text"].casefold().strip())
            should_resolve = mention["candidate_type"] == "Company" and normalized in aliases[ticker]
            require((mention["resolved_entity"] is not None) == should_resolve,
                    "Issuer resolution does not follow the alias registry.")
            if should_resolve:
                require(mention["resolved_entity"] == {
                    "label": "Company", "cik": section["cik"], "legal_name": section["company_name"]
                }, "Resolved issuer differs from filing metadata.")
                require(mention["resolution_status"] == "resolved_by_filing_context",
                        "Resolved mention status differs.")
            else:
                require(mention["resolution_status"] == (
                    "needs_review" if mention["candidate_type"] == "RiskFactor" else "unresolved"
                ), "Unresolved mention status differs.")
        resolved_counts[ticker] = sum(m["resolved_entity"] is not None for m in mentions)
        sections[section["accession_number"]] = section

    paths = read("verified_paths.json")
    require(len(paths) == len(all_claims) == verification["path_count"]
            == verification["claim_count"] == 38, "Unexpected path coverage.")
    require({p["claim_id"] for p in paths} == set(all_claims), "Missing or duplicate Claim paths.")
    require(verification["counts"] == {"nodes": 47, "relationships": 44},
            "Unexpected recorded live counts.")
    require(sum(c["cross_block"] for c in all_claims.values())
            == verification["cross_block_path_count"] == 1, "Cross-block count differs.")
    for key in ("repeated_loading", "path_provenance", "simulated_chunk_metadata_changes", "simulation_cleanup"):
        require(verification[key] == "passed", f"Recorded live check did not pass: {key}")
    for path in paths:
        claim = all_claims[path["claim_id"]]
        section = sections[claim["accession_number"]]
        require(path["quote"] == claim["quote"] and path["issuer_cik"] == claim["issuer_cik"]
                and path["cross_block"] == claim["cross_block"], "Path Claim differs.")
        hops = path["hops"]
        require([h["type"] for h in hops] == ["FILES", "CONTAINS", "SUPPORTS"], "Path structure differs.")
        expected_edges = [
            stable_id("edge", section["cik"], "FILES", section["accession_number"], claim["section_id"]),
            stable_id("edge", section["accession_number"], "CONTAINS", claim["section_id"], claim["section_id"]),
            stable_id("edge", claim["section_id"], "SUPPORTS", claim["claim_id"], claim["section_id"]),
        ]
        for hop, edge_id in zip(hops, expected_edges):
            require(hop["edge_id"] == edge_id and hop["prototype_id"] == verification["prototype_id"],
                    "Relationship identity differs.")
            require(all(hop[key] == claim[key] for key in (
                "accession_number", "section_id", "source_url", "source_document_sha256"
            )), "Hop source provenance differs.")
        require(json.loads(hops[1]["source_locator_json"])["xpath"] == section["start_heading_xpath"],
                "Section heading locator differs.")
        for field in ("evidence_fragments", "context_evidence_fragments"):
            require(json.loads(hops[-1][f"{field}_json"]) == claim[field], "Support evidence differs.")
    with (data / "review_queue.csv").open(encoding="utf-8", newline="") as file:
        queue = list(csv.DictReader(file))
    require(len(queue) == len(all_claims)
            and {r["claim_id"] for r in queue} == set(all_claims), "Review queue coverage differs.")
    for row in queue:
        claim = all_claims[row["claim_id"]]
        for field in ("claim_type", "quote", "context", "annotation_note", "extraction_status"):
            require(row[field] == (claim[field] or ""), f"Review field differs: {field}")
        require(json.loads(row["review_flags"]) == claim["review_flags"]
                and row["cross_block"] == str(claim["cross_block"]), "Review flags differ.")
    findings = json.loads((directory / "review_findings.json").read_text(encoding="utf-8"))
    require(all(f["status"] == "open" for f in findings), "Review findings were silently closed.")
    print("Saved-result consistency checks passed: 38 Claims, 38 paths, 1 cross-block Claim.")
    print("Recorded live graph: 47 nodes, 44 relationships.")
    print(f"Resolved issuer mentions: {resolved_counts}")
    print(f"Open quality findings: {len(findings)}; extraction completeness is not established.")
    print("Offline check only; no Claude, SEC or Neo4j calls were made.")


if __name__ == "__main__":
    main()
