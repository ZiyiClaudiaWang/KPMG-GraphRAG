"""Offline consolidation of the saved pilot and gap-review checkpoint.

No network, API, or database operations. All classifications remain needs_review.
"""

import argparse
import copy
import csv
import hashlib
import json
import re
import runpy
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def stable_id(prefix, *parts):
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return prefix + "_" + hashlib.sha256(payload).hexdigest()[:24]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def check_record(record, section, assemble):
    blocks = {b["block_order"]: b for b in section["blocks"]}
    positions = {b["block_order"]: i for i, b in enumerate(section["blocks"])}
    for field, text_field in [("evidence_fragments", "quote"),
                              ("context_evidence_fragments", "context")]:
        fragments = record[field]
        if not fragments:
            if field == "evidence_fragments" or record[text_field] is not None:
                raise ValueError("Missing source evidence or unsupported context.")
            continue
        quote, locations = assemble(fragments, blocks, positions, set(blocks))
        if quote != record[text_field]:
            raise ValueError("Record text differs from source evidence.")
        for fragment, location in zip(fragments, locations):
            if any(fragment[k] != location[k] for k in location):
                raise ValueError("Saved source location differs from reconstructed location.")
    expected_id = stable_id("claim", section["accession_number"], section["section_key"],
                            normalize(record["quote"]))
    expected = {
        "claim_id": expected_id, "issuer_cik": section["cik"],
        "accession_number": section["accession_number"],
        "section_id": section["accession_number"] + "/" + section["section_key"],
        "section_key": section["section_key"], "source_url": section["source_url"],
        "source_document_sha256": section["sha256"], "extraction_status": "needs_review",
        "cross_block": len(record["evidence_fragments"]) > 1,
    }
    if any(record[k] != value for k, value in expected.items()):
        raise ValueError("Claim identity or source provenance differs.")
    if record["claim_type"] not in {"stated_fact", "forward_looking", "hedged_risk"}:
        raise ValueError("Unknown Claim classification.")


def prepare(root, output=None):
    root = Path(root)
    output = Path(output) if output else root / "gap_consolidation"
    # Execute only the known helper shipped with this checkpoint, not notebook cells.
    helper = root / "code" / "gap_review_fix.py"
    if not helper.exists():
        helper = Path(__file__).with_name("gap_review_fix.py")
    if not helper.exists():
        helper = Path.cwd() / "gap_review_fix.py"
    utilities = runpy.run_path(str(helper))
    assemble = utilities["assemble"]
    process_review = utilities["process_review"]
    gaps = read(root / "coverage_candidates.json")
    sections = {t: read(root / t / "item1a.json") for t in ("AAPL", "MSFT", "JPM")}
    candidates = {}
    deferred = []
    assessments = []
    mentions = []
    duplicates = []
    decisions = Counter()
    draft_count = 0
    cache_files = []
    origin_by_id = {}
    for ticker, section in sections.items():
        blocks = {b["block_order"]: b for b in section["blocks"]}
        positions = {b["block_order"]: i for i, b in enumerate(section["blocks"])}
        baseline = read(root / "quality_revision" / (ticker + "_claims.json"))
        candidates[ticker] = copy.deepcopy(baseline)
        by_id = {}
        for record in candidates[ticker]:
            check_record(record, section, assemble)
            if record["claim_id"] in by_id:
                raise ValueError("Duplicate baseline Claim identity.")
            by_id[record["claim_id"]] = record
            origin_by_id[record["claim_id"]] = {"stage": "quality_revision1"}
        gaps_by_id = {g["gap_id"]: g for g in gaps[ticker]}
        seen_gaps = []
        for gap in gaps[ticker]:
            block = blocks[gap["block_order"]]
            expected_id = stable_id("review_gap", section["accession_number"], section["section_key"],
                                    gap["block_order"], gap["start"], gap["end"])
            if (gap["gap_id"] != expected_id or
                    block["text"][gap["start"]:gap["end"]] != gap["text"] or
                    gap["source_locator"] != block["source_locator"] or
                    gap["source_url"] != section["source_url"] or
                    gap["accession_number"] != section["accession_number"]):
                raise ValueError("Gap identity or source span differs.")
        for path in sorted((root / "gap_reviews").glob(ticker + "_*.json")):
            saved = read(path)
            request = saved["request"]
            fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            if saved["request_hash"] != fingerprint or path.name != ticker + "_" + fingerprint[:16] + ".json":
                raise ValueError("Cached request identity differs.")
            if (request["source_sha256"] != section["sha256"] or
                    request["accession_number"] != section["accession_number"]):
                raise ValueError("Cached request source differs.")
            targets = request["packet"]["targets"]
            for target in targets:
                expected = gaps_by_id[target["gap_id"]]
                if any(target[k] != expected[k] for k in ("gap_id", "block_order", "start", "end", "text")):
                    raise ValueError("Cached target differs from saved gap.")
            supplied = request["packet"]["blocks"]
            for block in supplied:
                if block["text"] != blocks[block["block_order"]]["text"]:
                    raise ValueError("Cached input differs from section text.")
            allowed = {b["block_order"] for b in supplied}
            replay, source_audit, _ = process_review(
                saved.get("raw_review", saved["review"]), section, targets, allowed, ticker
            )
            if replay != saved["review"]:
                raise ValueError("Saved processed response differs from source-validation replay.")
            if source_audit != saved.get("source_validation_audit", []):
                raise ValueError("Saved deferred-draft audit differs from replay.")
            deferred.extend(dict(d, ticker=ticker, review_file=path.name,
                                 stage="source_validation") for d in source_audit)
            cache_files.append(path.name)
            for assessment in replay["assessments"]:
                gap_id = assessment["gap_id"]
                seen_gaps.append(gap_id)
                decisions[assessment["decision"]] += 1
                assessments.append(dict(assessment, ticker=ticker, review_file=path.name))
                for mention in assessment["mentions"]:
                    mentions.append({"ticker": ticker, "gap_id": gap_id,
                                     "review_file": path.name, "mention": mention,
                                     "resolution_status": "not_run", "review_status": "needs_review"})
                for draft in assessment["claims"]:
                    draft_count += 1
                    incomplete = (draft["quote"].rstrip().endswith(":") or
                                  ("•" in draft["quote"] and
                                   not re.search(r"[.!?][\u201d\u2019\"']?$", draft["quote"].rstrip())))
                    if incomplete or assessment["decision"] != "extract":
                        deferred.append({"ticker": ticker, "gap_id": gap_id,
                                         "review_file": path.name, "stage": "boundary_screen",
                                         "reason": "Incomplete list boundary or assessment still needs review.",
                                         "draft": copy.deepcopy(draft)})
                        continue
                    quote, locations = assemble(draft["evidence_fragments"], blocks, positions, allowed)
                    context_locations = []
                    if draft["context_fragments"]:
                        context, context_locations = assemble(draft["context_fragments"], blocks, positions, allowed)
                        if context != draft["context"]:
                            raise ValueError("Context rendering differs.")
                    claim_id = stable_id("claim", section["accession_number"], section["section_key"], normalize(quote))
                    flags = []
                    if re.match(r'^[“"].{1,100}?[”"]\s+(?:means|include)', quote, re.I):
                        flags.append("definition_candidate")
                    if re.search(r"\b(?:has|have|were|was|past|announced|imposed)\b", quote, re.I) and re.search(
                            r"\b(?:may|could|future|must)\b", quote, re.I):
                        flags.append("mixed_modality_candidate")
                    if not draft["context"] and re.match(r"^(?:These|This|Such|They|It)\b", quote):
                        flags.append("context_review_candidate")
                    record = {
                        "claim_id": claim_id, "quote": quote, "claim_type": draft["claim_type"],
                        "subject_scope": draft["subject_text"], "context": draft["context"],
                        "annotation_note": draft["annotation_note"], "review_flags": flags,
                        "extraction_status": "needs_review", "issuer_cik": section["cik"],
                        "accession_number": section["accession_number"],
                        "section_id": section["accession_number"] + "/" + section["section_key"],
                        "section_key": section["section_key"],
                        "section_identity_status": section["identity_status"],
                        "source_url": section["source_url"], "source_document_sha256": section["sha256"],
                        "evidence_fragments": locations, "context_evidence_fragments": context_locations,
                        "cross_block": len(locations) > 1,
                        "quote_text_basis": "normalized_dom_fragments_joined_with_spaces",
                        "source_chunk_id": None, "extraction_model": saved["response_model"],
                        "extraction_request_hash": fingerprint,
                    }
                    check_record(record, section, assemble)
                    if claim_id in by_id:
                        old = by_id[claim_id]
                        if any(old[k] != record[k] for k in ("quote", "evidence_fragments", "context", "claim_type")):
                            raise ValueError("Repeated identity has conflicting source or classification.")
                        duplicates.append({"claim_id": claim_id, "gap_id": gap_id, "review_file": path.name})
                        continue
                    candidates[ticker].append(record)
                    by_id[claim_id] = record
                    origin_by_id[claim_id] = {"stage": "gap_review", "gap_id": gap_id, "review_file": path.name}
        if len(seen_gaps) != len(gaps_by_id) or set(seen_gaps) != set(gaps_by_id):
            raise ValueError("Gap assessments are missing or duplicated across caches.")

    section = sections["JPM"]
    blocks = {b["block_order"]: b for b in section["blocks"]}
    groups = []
    for start, end in [(26, 34), (35, 40), (43, 46), (49, 56), (57, 63)]:
        fragments = [{"block_order": i, "quote_fragment": blocks[i]["text"]} for i in range(start, end + 1)]
        quote, locations = assemble(fragments, blocks,
                                    {b["block_order"]: i for i, b in enumerate(section["blocks"])}, set(blocks))
        groups.append({"review_group_id": "JPM_list_" + str(start) + "_" + str(end),
                       "record_type": "source_evidence_group", "quote": quote,
                       "accession_number": section["accession_number"], "source_url": section["source_url"],
                       "source_document_sha256": section["sha256"], "evidence_fragments": locations,
                       "claim_boundary_decision": "pending", "review_status": "needs_review",
                       "extends_beyond_initial_excerpt": end > 57,
                       "note": "Preserved complete source group; not a Claim approved for graph loading."})
    summary = {
        "source_validation": "passed_for_candidate_records", "gap_assessments": len(assessments),
        "recorded_gap_decisions": dict(decisions), "gap_review_retained_drafts_before_boundary_screen": draft_count,
        "deferred_source_drafts": sum(d["stage"] == "source_validation" for d in deferred),
        "deferred_retained_boundary_drafts": sum(d["stage"] == "boundary_screen" for d in deferred),
        "duplicate_candidates": len(duplicates), "candidate_claims": {t: len(c) for t, c in candidates.items()},
        "total_candidate_claims": sum(map(len, candidates.values())),
        "complete_list_source_groups": len(groups), "review_cache_files": cache_files,
        "review_status": "needs_review", "database_updated": False,
        "scope": "Selected excerpts from three filings; not complete Item 1A extraction.",
        "limits": ["Source alignment does not establish classification accuracy or completeness.",
                   "Gap discovery uses an 80-character text-gap heuristic, not sentence recall.",
                   "Source spans address whitespace-normalized block text, not raw HTML offsets.",
                   "Raw HTML is absent from this checkpoint; recorded SEC hashes are cross-checked, not recomputed.",
                   "Mention candidates remain unresolved; only prior baseline issuer resolution is available.",
                   "List groups are evidence for a pending boundary policy, not additional Claim nodes."],
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [("candidate_claims_by_ticker", candidates), ("summary", summary),
                        ("deferred_drafts", deferred), ("boundary_review_groups", groups),
                        ("gap_assessments", assessments), ("mention_candidates", mentions),
                        ("duplicate_candidates", duplicates)]:
        write(output / (name + ".json"), value)
    with (output / "review_queue.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = ["ticker", "claim_id", "claim_type", "quote", "context", "review_flags", "review_status", "origin"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for ticker, records in candidates.items():
            for record in records:
                writer.writerow({"ticker": ticker, "claim_id": record["claim_id"],
                                 "claim_type": record["claim_type"], "quote": record["quote"],
                                 "context": record["context"], "review_flags": ";".join(record["review_flags"]),
                                 "review_status": "needs_review", "origin": json.dumps(origin_by_id[record["claim_id"]])})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.checkpoint, args.output), indent=2))
