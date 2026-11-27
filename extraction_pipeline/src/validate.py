"""Step 8 - Validate: check labels for completeness, then hold doubtful ones for review.

Input : data/output/sentences.jsonl, data/output/labels.jsonl, data/sections/*.jsonl,
        data/entity_catalog.json (optional)
Output: data/output/errors.jsonl         every unit must have exactly one valid label (empty when passed)
        data/output/review_queue.jsonl   units for a person to look at
        data/output/provisional_segment_definitions.json   segment names read from the filings

Completeness: the same checks run inside step 7 on each reply (check_reply). A rejected
reply publishes no labels, so its sentences show up here as "no label". Units set aside in
step 7 (source "code_quarantine", label needs_review) are held here like any needs_review.

Review follows Claudia's build_candidates() and source_review.py, with her flag names. Two
kinds of queue entry:
  held          label needs_review: the model could not resolve the boundary, subject or type
                (also step 7's quarantine). Held units do not become Claims in step 9.
  retained      the Claim is built, but a rule flags it: claim_review_flags
                (source_review.claim_review_flags(): mixed tense, "causes / has required ...
                may", "is subject to laws" as hedged_risk) and / or entity flags. The Claim
                carries claim_review_flags too. Rule flags hold nothing since 27 Nov 2026.
List items and list groups are Claims like any sentence: a ":" intro among the context is no
longer a flag, and the MSFT FY2026 product-line prefixes (review.known_list_fragment_prefixes)
are gone.

A unit made of several source fragments (a list group, or a sentence joined across a page
break) is not held for that reason: its text is rebuilt exactly from the source in step 7,
and whether it was joined correctly is a question for steps 2 and 4, not for the label. Its
Claim carries multi_fragment = true and every fragment's position (step 9).

Every record here (errors, queue entries, the intro units they cite) names its unit by unit_id
(#45), so a review decision still points at the same unit after other sentences are re-split.
"""

from __future__ import annotations

import json
from collections import Counter

from . import source_review
from .entities import annotation as entity_annotation
from .common import Paths, data_dir, log, read_jsonl, write_jsonl

STEP = "validate"

CLAIM_TYPES = ("stated_fact", "forward_looking", "hedged_risk")   # ontology decision 2.3
# Pipeline-only labels, never Claim types: not_a_claim (not a statement) and
# needs_review (boundary, subject or type the model could not resolve).
LABELS = CLAIM_TYPES + ("not_a_claim", "needs_review")


def check_reply(batch: dict, results: list[dict]) -> list[str]:
    """Problems with one LLM reply, as short messages the model can act on."""
    problems = []
    expected = batch["local_ids"]
    got = [r.get("id") for r in results]
    allowed_context = set(batch["context_local_ids"]) | set(expected)
    order = {sid: i for i, sid in enumerate(batch["context_local_ids"] + expected)}

    missing = [i for i in expected if i not in got]
    extra = [i for i in got if i not in expected]
    dupes = [i for i, n in Counter(got).items() if n > 1]
    if missing:
        problems.append(f"missing ids: {', '.join(missing)}")
    if extra:
        problems.append(f"unknown ids: {', '.join(map(str, extra))}")
    if dupes:
        problems.append(f"duplicate ids: {', '.join(dupes)}")
    for r in results:
        sid = r.get("id")
        if r.get("label") not in LABELS:
            problems.append(f"{sid}: label must be one of {', '.join(LABELS)}")
        context = r.get("context_ids") or []
        if len(context) != len(set(context)):
            problems.append(f"{sid}: duplicate context ids")
        for cid in context:
            if cid not in allowed_context:
                problems.append(f"{sid}: context id {cid} is not in the input")
            elif sid in order and order[cid] >= order[sid]:
                problems.append(f"{sid}: context id {cid} must come before the sentence")
        known = [order[c] for c in context if c in order]
        if known != sorted(known):
            problems.append(f"{sid}: context ids must be in source order")
    return problems


# ---------- review ----------

HELD_UNCERTAIN_REASON = "Model uncertainty; preserve for review."
RETAINED_REASON = "Claim source is valid; rule or entity flags for review."


def assessment(label: dict) -> dict:
    """The model's row for a unit in Claudia's shape; ids are unit ids."""
    return {"id": label["unit_id"], "label": label["label"],
            "context_ids": list(label["context_unit_ids"]),
            "subject_scope": label["subject_scope"], "annotation_note": label["annotation_note"],
            "is_risk_heading": label["is_risk_heading"], "entity_mentions": label["entity_mentions"]}


def review_entry(unit: dict, result: dict, source: dict, vocabulary: list[dict],
                 catalog: list[dict]) -> dict | None:
    """Per-unit decision: held (needs_review), retained with rule or entity flags, or None (clean)."""
    if result["label"] == "not_a_claim":
        return None
    ids = {"unit_id": unit["unit_id"]}
    fragments = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b in unit["raw_spans"]]
    if result["label"] == "needs_review":
        return {**ids, "assessment": result, "source_fragments": fragments, "reason": HELD_UNCERTAIN_REASON}
    annotated = entity_annotation.annotate_mentions(unit["text"], result["entity_mentions"],
                                                    issuer_cik=source["cik"],
                                                    accession=source["accession_number"], catalog=catalog)
    reviewed = source_review.entity_review(unit, source, result["entity_mentions"], vocabulary)
    flags = sorted(set(annotated["entity_review_flags"] + reviewed["review_flags"]))
    rule_flags = source_review.claim_review_flags(unit, result)
    if flags or rule_flags:
        return {**ids, "candidate_retained": True, "reason": RETAINED_REASON, "flags": flags,
                "claim_review_flags": rule_flags}
    return None


def review(units: dict[str, dict], labels: list[dict]) -> list[dict]:
    from .label import entity_catalog, source_index     # imported here: the label package imports this module
    sources, _ = source_index()
    vocabulary = source_review.source_vocabulary(sources)
    (data_dir() / "output").mkdir(parents=True, exist_ok=True)
    (data_dir() / "output" / "provisional_segment_definitions.json").write_text(
        json.dumps(vocabulary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = entity_catalog()

    queue = []
    for label in labels:
        unit = units.get(label["unit_id"])
        if unit is None or label.get("source", "").startswith("rule:"):
            continue
        key = (unit["accession_number"], unit["section_key"])
        entry = review_entry(unit, assessment(label), sources[key], vocabulary, catalog)
        if entry:
            queue.append(entry)
    return queue


def held_ids() -> set[str]:
    """Unit ids held from Claims by the last review (used by step 9)."""
    if not Paths.review_queue().exists():
        return set()
    return {r["unit_id"] for r in read_jsonl(Paths.review_queue()) if not r.get("candidate_retained")}


def run() -> int:
    units = {u["unit_id"]: u for u in read_jsonl(Paths.sentences())}
    labels = list(read_jsonl(Paths.labels())) if Paths.labels().exists() else []
    errors = []

    seen = Counter(l["unit_id"] for l in labels)
    for uid in units:
        if seen[uid] == 0:
            errors.append({"unit_id": uid, "error": "no label"})
        elif seen[uid] > 1:
            errors.append({"unit_id": uid, "error": f"{seen[uid]} labels"})
    for l in labels:
        uid = l["unit_id"]
        if uid not in units:
            errors.append({"unit_id": uid, "error": "label for unknown unit"})
        if l["label"] not in LABELS:
            errors.append({"unit_id": uid, "error": f"invalid label {l['label']!r}"})
        for cid in l.get("context_unit_ids") or []:
            if cid not in units:
                errors.append({"unit_id": uid, "error": f"unknown context unit {cid}"})

    write_jsonl(Paths.errors(), errors)
    labelled = sum(seen[uid] == 1 for uid in units)
    log(STEP, f"{labelled}/{len(units)} units have one label; {len(errors)} errors -> {Paths.errors()}")
    by_label = Counter(l["label"] for l in labels)
    log(STEP, "labels: " + ", ".join(f"{k}={v}" for k, v in sorted(by_label.items())))

    queue = review(units, labels)
    write_jsonl(Paths.review_queue(), queue)
    held = [q for q in queue if not q.get("candidate_retained")]
    log(STEP, f"{len(held)} units held (needs_review), {len(queue) - len(held)} Claims with flags "
              f"-> {Paths.review_queue()}")
    flagged = Counter(f for q in queue for f in q.get("claim_review_flags") or [])
    if flagged:
        log(STEP, "rule flags: " + ", ".join(f"{k}={v}" for k, v in flagged.most_common()))
    return len(errors)


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
