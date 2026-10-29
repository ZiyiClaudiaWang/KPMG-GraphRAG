"""Step 6 - Validate: check labels for completeness, then hold doubtful ones for review.

Input : data/output/sentences.jsonl, data/output/labels.jsonl, data/sections/*.jsonl,
        data/entity_catalog.json (optional)
Output: data/output/errors.jsonl         every unit must have exactly one valid label (empty when passed)
        data/output/review_queue.jsonl   units for a person to look at
        data/output/provisional_segment_definitions.json   segment names read from the filings

Completeness: the same checks run inside step 5 on each reply (check_reply). A rejected
reply publishes no labels, so its sentences show up here as "no label". Units set aside in
step 5 (source "code_quarantine", label needs_review) are held here like any needs_review.

Review follows Claudia's build_candidates() and source_review.py, with her reasons and
flag names. Three kinds of queue entry:
  held, rule flags     source_review.claim_review_flags() (list intro, mixed tense,
                       "causes / has required ... may", "is subject to laws" as risk), plus
                       known product-line fragments (review.known_list_fragment_prefixes)
  held, uncertainty    label needs_review, or text that is not one exact piece of the source
                       (a list group, or a sentence joined across a page break)
  retained             the Claim is built, but its entity mentions need review
Held units do not become Claims in step 7b; they keep their label.
"""

from __future__ import annotations

import json
from collections import Counter

from . import entity_annotation, source_review
from .common import Paths, data_dir, log, read_jsonl, settings, write_jsonl

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

HELD_RULE_REASON = "Conservative postcheck; original model label retained"
HELD_UNCERTAIN_REASON = "Model uncertainty, grouped list or non-verbatim display text; preserve for review."
RETAINED_REASON = "Claim source is valid; entity mention evidence requires review."


def assessment(label: dict) -> dict:
    """The model's row for a unit, with local ids, as it came back in the reply."""
    prefix = label["sentence_id"].rsplit("|", 1)[0] + "|"
    return {"id": label["sentence_id"].rsplit("|", 1)[1], "label": label["label"],
            "context_ids": [c[len(prefix):] if c.startswith(prefix) else c for c in label["context_ids"]],
            "subject_scope": label["subject_scope"], "annotation_note": label["annotation_note"],
            "is_risk_heading": label["is_risk_heading"], "entity_mentions": label["entity_mentions"]}


def review_entry(unit: dict, result: dict, units: dict[str, dict], neighbours: list[dict], source: dict,
                 vocabulary: list[dict], catalog: list[dict], prefixes: tuple) -> dict | None:
    """Claudia's per-unit decision: held (two kinds), retained with entity flags, or None (clean)."""
    flags, intros = source_review.claim_review_flags(unit, result, units)
    pos = next(i for i, u in enumerate(neighbours) if u["sentence_id"] == unit["sentence_id"])
    if prefixes and any(u["text"].rstrip().endswith(":") for u in neighbours[max(0, pos - 4):pos]) \
            and unit["text"].startswith(prefixes):
        flags.append("known_product_list_fragment_boundary_review")
    if flags and result["label"] != "not_a_claim":
        return {"sentence_id": unit["sentence_id"], "assessment": result, "reason": HELD_RULE_REASON,
                "claim_review_flags": sorted(set(flags)), "intro_sentence_ids": intros}
    if result["label"] == "not_a_claim":
        return None
    fragments = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b in unit["raw_spans"]]
    if result["label"] == "needs_review" or len(fragments) != 1 or fragments[0]["quote"] != unit["text"]:
        return {"sentence_id": unit["sentence_id"], "assessment": result, "source_fragments": fragments,
                "reason": HELD_UNCERTAIN_REASON}
    annotated = entity_annotation.annotate_mentions(unit["text"], result["entity_mentions"],
                                                    issuer_cik=source["cik"],
                                                    accession=source["accession_number"], catalog=catalog)
    reviewed = source_review.entity_review(unit, source, result["entity_mentions"], vocabulary)
    flags = sorted(set(annotated["entity_review_flags"] + reviewed["review_flags"]))
    if flags:
        return {"sentence_id": unit["sentence_id"], "candidate_retained": True, "reason": RETAINED_REASON,
                "flags": flags}
    return None


def review(units: dict[str, dict], labels: list[dict]) -> list[dict]:
    from .label import entity_catalog, source_index     # imported here: label.py imports this module
    sources, _ = source_index()
    vocabulary = source_review.source_vocabulary(sources)
    (data_dir() / "output").mkdir(parents=True, exist_ok=True)
    (data_dir() / "output" / "provisional_segment_definitions.json").write_text(
        json.dumps(vocabulary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = entity_catalog()
    prefixes = tuple((settings().get("review") or {}).get("known_list_fragment_prefixes") or [])
    sections: dict[tuple, list[dict]] = {}
    for u in units.values():
        sections.setdefault((u["accession_number"], u["section_key"]), []).append(u)

    queue = []
    for label in labels:
        unit = units.get(label["sentence_id"])
        if unit is None or label.get("source", "").startswith("rule:"):
            continue
        key = (unit["accession_number"], unit["section_key"])
        entry = review_entry(unit, assessment(label), units, sections[key], sources[key],
                             vocabulary, catalog, prefixes)
        if entry:
            queue.append(entry)
    return queue


def held_ids() -> set[str]:
    """Sentence ids held from Claims by the last review (used by step 7b)."""
    if not Paths.review_queue().exists():
        return set()
    return {r["sentence_id"] for r in read_jsonl(Paths.review_queue()) if not r.get("candidate_retained")}


def run() -> int:
    units = {u["sentence_id"]: u for u in read_jsonl(Paths.sentences())}
    labels = list(read_jsonl(Paths.labels())) if Paths.labels().exists() else []
    errors = []

    seen = Counter(l["sentence_id"] for l in labels)
    for sid in units:
        if seen[sid] == 0:
            errors.append({"sentence_id": sid, "error": "no label"})
        elif seen[sid] > 1:
            errors.append({"sentence_id": sid, "error": f"{seen[sid]} labels"})
    for l in labels:
        sid = l["sentence_id"]
        if sid not in units:
            errors.append({"sentence_id": sid, "error": "label for unknown sentence"})
        if l["label"] not in LABELS:
            errors.append({"sentence_id": sid, "error": f"invalid label {l['label']!r}"})
        for cid in l.get("context_ids") or []:
            if cid not in units:
                errors.append({"sentence_id": sid, "error": f"unknown context id {cid}"})

    write_jsonl(Paths.errors(), errors)
    labelled = sum(seen[sid] == 1 for sid in units)
    log(STEP, f"{labelled}/{len(units)} units have one label; {len(errors)} errors -> {Paths.errors()}")
    by_label = Counter(l["label"] for l in labels)
    log(STEP, "labels: " + ", ".join(f"{k}={v}" for k, v in sorted(by_label.items())))

    queue = review(units, labels)
    write_jsonl(Paths.review_queue(), queue)
    held = [q for q in queue if not q.get("candidate_retained")]
    log(STEP, f"{len(held)} units held, {len(queue) - len(held)} Claims with entity flags "
              f"-> {Paths.review_queue()}")
    reasons = Counter(f for q in held for f in q.get("claim_review_flags") or ["uncertain_or_non_verbatim"])
    if reasons:
        log(STEP, "held by: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))
    return len(errors)


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
