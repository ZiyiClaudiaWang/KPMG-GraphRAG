"""Check saved pilot consistency offline; does not rerun Claude or Neo4j."""

import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stable_id(prefix, *parts):
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def main():
    directory = Path(__file__).resolve().parent / "results"

    def read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    source = read("source_metadata.json")
    excerpt = read("source_excerpt.json")
    model = read("model_extraction.json")
    claims = read("claims.json")
    mentions = read("mention_resolution.json")
    verification = read("verification.json")
    runtime = read("runtime.json")
    schema = read("extraction_schema.json")
    blocks = {block["block_order"]: block for block in excerpt["blocks"]}
    target = blocks[excerpt["target_block_order"]]
    accession = source["accession_number"]
    section_id = f"{accession}/{excerpt['section_key']}"
    expected_hash = hashlib.sha256(
        json.dumps(model["request"], sort_keys=True).encode()
    ).hexdigest()
    require(expected_hash == model["request_hash"] == runtime["request_hash"],
            "Request hashes differ.")
    require(model["request"]["schema"] == schema, "Schema differs from request.")
    require(model["model"] == runtime["model"] == model["request"]["model"],
            "Recorded model identities differ.")
    require(model["source_metadata"] == source, "Source metadata differs.")
    require(model["request"]["source_sha256"] == excerpt["source_document_sha256"]
            == source["sha256"], "Source checksums differ.")
    require(model["request"]["payload"]["blocks"] == [
        {"block_order": b["block_order"], "text": b["text"]}
        for b in excerpt["blocks"]
    ], "Recorded prompt does not match saved source blocks.")
    require(len(claims) == len(model["extraction"]["claims"]) == 3,
            "Expected three pilot Claims.")
    require(len(mentions) == len(model["extraction"]["mentions"]) == 4,
            "Expected four pilot mentions.")

    for claim, draft in zip(claims, model["extraction"]["claims"]):
        require(claim["quote"] == draft["quote"] and bool(claim["quote"]),
                "Claim quote differs from model output.")
        require(claim["claim_type"] == draft["claim_type"] == "stated_fact",
                "Unexpected pilot classification.")
        require(claim["block_order"] == draft["block_order"] == target["block_order"],
                "Unexpected Claim source block.")
        require(claim["extraction_status"] == "needs_review", "Review status changed.")
        require(claim["accession_number"] == accession
                and claim["section_id"] == section_id, "Claim provenance differs.")
        require(claim["source_locator"] == target["source_locator"], "XPath differs.")
        require(claim["claim_id"] == stable_id(
            "claim", accession, excerpt["section_key"], " ".join(claim["quote"].split())
        ), "Claim ID differs from the deterministic rule.")
        require(bool(claim["source_spans"]), "Missing Claim source spans.")
        for span in claim["source_spans"]:
            require(0 <= span["start"] < span["end"] <= len(target["text"]),
                    "Source span is out of bounds.")
            require(target["text"][span["start"]:span["end"]] == claim["quote"],
                    "Source span does not reproduce the quote.")

    for mention, draft in zip(mentions, model["extraction"]["mentions"]):
        require(all(mention[key] == draft[key] for key in draft),
                "Resolved mention differs from its extraction.")
        require(bool(mention["source_spans"]), "Missing mention source spans.")
        for span in mention["source_spans"]:
            require(target["text"][span["start"]:span["end"]] == mention["surface_text"],
                    "Mention span does not reproduce source text.")
    resolved = [m for m in mentions if m["resolved_entity"] is not None]
    require(len(resolved) == 1 and resolved[0]["surface_text"] == "The Company"
            and resolved[0]["resolved_entity"]["cik"] == source["cik"],
            "Issuer resolution differs from the recorded rule.")

    by_id = {claim["claim_id"]: claim for claim in claims}
    paths = verification["paths"]
    require(len(by_id) == len(paths) == verification["path_count"] == 3,
            "Unexpected saved path count.")
    require({p["claim_id"] for p in paths} == set(by_id), "Path coverage differs.")
    require(verification["counts"] == {"nodes": 6, "relationships": 5},
            "Unexpected recorded graph counts.")
    require(all(verification[k] == "passed" for k in (
        "repeated_loading", "simulated_chunk_metadata_changes", "simulation_cleanup"
    )), "Recorded live checks did not pass.")
    for path in paths:
        claim = by_id[path["claim_id"]]
        require(path["quote"] == claim["quote"], "Path quote differs.")
        require([h["type"] for h in path["hops"]] == ["FILES", "CONTAINS", "SUPPORTS"],
                "Unexpected path structure.")
        expected_edges = [
            stable_id("edge", source["cik"], "FILES", accession, section_id),
            stable_id("edge", accession, "CONTAINS", section_id, section_id),
            stable_id("edge", section_id, "SUPPORTS", claim["claim_id"], section_id),
        ]
        for hop, edge_id in zip(path["hops"], expected_edges):
            require(hop["edge_id"] == edge_id, "Relationship identity differs.")
            require(hop["accession_number"] == accession
                    and hop["section_id"] == section_id
                    and bool(hop["source_url"]), "Hop provenance is incomplete.")
            require(hop["source_document_sha256"] == source["sha256"],
                    "Hop source checksum differs.")
        support = path["hops"][-1]
        require(json.loads(support["source_locator_json"]) == claim["source_locator"]
                and json.loads(support["source_spans_json"]) == claim["source_spans"],
                "Supporting relationship evidence differs from Claim evidence.")
    print("Saved pilot consistency checks passed: 4 mentions, 3 Claims, 3 paths.")
    print("Recorded live graph: 6 nodes, 5 relationships.")
    print("Offline check only; Claude and Neo4j were not called.")


if __name__ == "__main__":
    main()
