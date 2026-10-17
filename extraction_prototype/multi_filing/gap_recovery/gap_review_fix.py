"""Install a cache-preserving source validator in the existing Colab notebook."""

import copy
import hashlib
import json
import re


def assemble(fragments, blocks, positions, allowed):
    if not fragments:
        raise ValueError("Missing evidence fragments.")
    orders = [f["block_order"] for f in fragments]
    if any(order not in allowed for order in orders):
        raise ValueError("Evidence refers outside the supplied input.")
    indexes = [positions[order] for order in orders]
    if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
        raise ValueError("Evidence blocks must be consecutive and ordered.")
    locations = []
    for i, fragment in enumerate(fragments):
        text = fragment["quote_fragment"]
        block = blocks[fragment["block_order"]]
        if not text.strip():
            raise ValueError("Empty source fragment.")
        matches = list(re.finditer(re.escape(text), block["text"]))
        if len(matches) != 1:
            raise ValueError("Source fragment must have exactly one match.")
        start, end = matches[0].span()
        if len(fragments) > 1:
            if i > 0 and start != 0:
                raise ValueError("Cross-block evidence skips leading text.")
            if i < len(fragments) - 1 and end != len(block["text"]):
                raise ValueError("Cross-block evidence skips trailing text.")
        locations.append({
            "block_order": fragment["block_order"],
            "quote_fragment": text,
            "source_locator": block["source_locator"],
            "start": start, "end": end,
            "span_coordinate_system": "normalized_block_text",
        })
    return " ".join(f["quote_fragment"] for f in fragments), locations


def process_review(raw_review, section, targets, allowed, ticker):
    """Retain source-valid proposals; defer invalid proposals with their full audit."""
    review = copy.deepcopy(raw_review)
    targets_by_id = {g["gap_id"]: g for g in targets}
    ids = [a["gap_id"] for a in review["assessments"]]
    if len(ids) != len(targets_by_id) or set(ids) != set(targets_by_id):
        raise ValueError("Missing, duplicate or unexpected gap assessment IDs.")
    blocks = {b["block_order"]: b for b in section["blocks"]}
    positions = {b["block_order"]: i for i, b in enumerate(section["blocks"])}
    deferred = []
    rendered = []
    for assessment in review["assessments"]:
        gap = targets_by_id[assessment["gap_id"]]
        retained = []
        issues = []
        if assessment["decision"] == "exclude" and assessment["claims"]:
            issues.append("Excluded gap returned Claim proposals.")
        for claim in assessment["claims"]:
            original = copy.deepcopy(claim)
            try:
                # Source-specific list policy reviewed against JPM blocks 43-46.
                if ticker == "JPM" and any(
                    f["block_order"] in {44, 45, 46} for f in claim["evidence_fragments"]
                ):
                    raise ValueError("JPM list requires complete-group review (blocks 43-46).")
                quote, locations = assemble(claim["evidence_fragments"], blocks, positions, allowed)
                if not claim["quote"].strip() or quote != claim["quote"]:
                    raise ValueError("Claim quote differs from exact source fragments.")
                if not any(f["block_order"] == gap["block_order"] and
                           max(f["start"], gap["start"]) < min(f["end"], gap["end"])
                           for f in locations):
                    raise ValueError("Claim does not overlap its target gap.")
                if claim["context_fragments"]:
                    context, context_locations = assemble(
                        claim["context_fragments"], blocks, positions, allowed
                    )
                    if claim["context"] != context:
                        rendered.append({
                            "gap_id": assessment["gap_id"], "claim_quote": claim["quote"],
                            "model_context": claim["context"], "source_context": context,
                            "source_locations": context_locations,
                            "method": "exact_context_fragment_join",
                        })
                    claim["context"] = context
                elif claim["context"] is not None:
                    raise ValueError("Context text has no supporting source fragments.")
                retained.append(claim)
            except ValueError as exc:
                issues.append(str(exc))
                deferred.append({"gap_id": assessment["gap_id"], "kind": "claim",
                                 "reason": str(exc), "draft": original})
        assessment["claims"] = retained
        valid_mentions = []
        for mention in assessment["mentions"]:
            order = mention["block_order"]
            if (order in allowed and mention["surface_text"].strip()
                    and mention["surface_text"] in blocks[order]["text"]):
                valid_mentions.append(mention)
            else:
                issues.append("Mention does not match supplied source.")
                deferred.append({"gap_id": assessment["gap_id"], "kind": "mention",
                                 "reason": issues[-1], "draft": copy.deepcopy(mention)})
        assessment["mentions"] = valid_mentions
        if assessment["decision"] == "extract" and not retained:
            issues.append("No source-valid Claim retained for extraction.")
        if not assessment["reason"].strip():
            issues.append("Model provided no decision explanation.")
        if issues:
            assessment["decision"] = "needs_review"
            assessment["reason"] = "Code source review: " + " ".join(dict.fromkeys(issues))
    return review, deferred, rendered


def install_gap_review(namespace):
    """Replace run_gap_review using the notebook's existing schema, client and inputs."""
    def run_gap_review(ticker, target_gaps):
        section = namespace["multi_sections"][ticker]
        blocks = section["blocks"]
        positions = {b["block_order"]: i for i, b in enumerate(blocks)}
        selected = set()
        for gap in target_gaps:
            p = positions[gap["block_order"]]
            selected.update(range(max(0, p - 2), min(len(blocks), p + 3)))
        supplied = [blocks[i] for i in sorted(selected)]
        packet = {
            "targets": [{k: g[k] for k in ("gap_id", "block_order", "start", "end", "text")}
                        for g in target_gaps],
            "blocks": [{"block_order": b["block_order"], "text": b["text"]} for b in supplied],
        }
        schema_class = namespace["GapReviewBatch"]
        request = {
            "model": namespace["MODEL"], "system": namespace["GAP_REVIEW_PROMPT"],
            "packet": packet, "schema": schema_class.model_json_schema(),
            "accession_number": section["accession_number"], "source_sha256": section["sha256"],
        }
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        path = namespace["GAP_DIR"] / f"{ticker}_{fingerprint[:16]}.json"
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["request"] != request or saved["request_hash"] != fingerprint:
                raise ValueError("Saved request identity differs.")
            print("Reused saved gap review.", flush=True)
        else:
            response = namespace["client"].with_options(timeout=180.0).messages.parse(
                model=namespace["MODEL"], max_tokens=10000,
                system=namespace["GAP_REVIEW_PROMPT"],
                messages=[{"role": "user", "content": json.dumps(packet, ensure_ascii=False)}],
                output_format=schema_class,
            )
            if response.stop_reason != "end_turn" or response.parsed_output is None:
                raise RuntimeError(f"Incomplete gap review: {response.stop_reason}")
            saved = {
                "request_hash": fingerprint, "request": request,
                "response_model": response.model, "usage": response.usage.model_dump(),
                "review": response.parsed_output.model_dump(), "review_status": "needs_review",
            }
            path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
        raw = copy.deepcopy(saved.get("raw_review", saved["review"]))
        schema_class.model_validate(raw)
        review, deferred, rendered = process_review(
            raw, section, target_gaps, {b["block_order"] for b in supplied}, ticker
        )
        schema_class.model_validate(review)
        saved.update({
            "raw_review": raw, "review": review, "source_validation_audit": deferred,
            "context_rendering_audit": rendered, "processing_version": "gap_source_validation_v2",
            "source_alignment_status": "passed", "source_alignment_scope": "retained_drafts_only",
            "assessment_coverage_status": "all_target_ids_returned", "review_status": "needs_review",
        })
        if ticker == "JPM" and any("complete-group review" in d["reason"] for d in deferred):
            saved["complete_list_source"] = [b for b in blocks if b["block_order"] in {43, 44, 45, 46}]
        path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
        if deferred:
            print(f"Source validation deferred {len(deferred)} draft(s); original output retained.", flush=True)
        return saved

    run_gap_review.processing_version = "gap_source_validation_v2"
    namespace["run_gap_review"] = run_gap_review
    return run_gap_review
