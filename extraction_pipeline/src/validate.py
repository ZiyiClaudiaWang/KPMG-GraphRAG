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
                may", "is subject to laws" as hedged_risk). The Claim carries
                claim_review_flags too. Rule flags hold nothing since 27 Nov 2026.
Entity mentions are not checked here: step 8b extracts them from the Claims this step keeps,
and step 9 checks them (entity_review_flags on the Claim).
List items and list groups are Claims like any sentence: a ":" intro among the context is no
longer a flag, and the MSFT FY2026 product-line prefixes (review.known_list_fragment_prefixes)
are gone.

A unit made of several source fragments (a list group, or a sentence joined across a page
break) is not held for that reason: its text is rebuilt exactly from the source in step 7,
and whether it was joined correctly is a question for steps 2 and 4, not for the label. Its
Claim carries multi_fragment = true and every fragment's position (step 9).

Manual decisions: config/label_overrides.csv (unit_id, label, reviewer, note[, context_unit_ids]) sets a unit's label
by hand, over the model's and the prefilter's: a Claim type releases a not_a_claim or held unit,
not_a_claim drops a Claim. Steps 8, 8b and 9 read labels through reviewed_labels(), so a released
unit is extracted in 8b and built in 9 on their next run; labels.jsonl (the model's) is never
changed. checks/validate/not_a_claim.csv lists every unit the model labelled not_a_claim, to
choose from. The override is per unit_id: a repeat in another filing keeps its own label.

Every record here (errors, queue entries, the intro units they cite) names its unit by unit_id
(#45), so a review decision still points at the same unit after other sentences are re-split.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path

from . import source_review
from .common import CONFIG_DIR, Paths, checks_dir, data_dir, log, read_jsonl, write_jsonl

STEP = "validate"

CLAIM_TYPES = ("stated_fact", "forward_looking", "hedged_risk")   # ontology decision 2.3
# Pipeline-only labels, never Claim types: not_a_claim (not a statement) and
# needs_review (boundary, subject or type the model could not resolve).
LABELS = CLAIM_TYPES + ("not_a_claim", "needs_review")
# Who or what a statement is mainly about (subject_scope, 1 to 3 values, the main one first).
SUBJECT_SCOPES = ("company", "segment", "subsidiary", "named_organization", "competitors", "suppliers",
                  "customers", "partners", "industry", "government", "macro", "other")
# The model's fields of a label (label_v3) besides id, label and context_ids, with the value a
# label made by code (a prefilter rule, quarantine) gets. labels.jsonl rows carry these keys.
LABEL_DEFAULTS = {"subject_scope": [], "annotation_note": None, "is_boilerplate": None, "time_reference": None}


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
RETAINED_REASON = "Claim source is valid; rule flags for review."


def assessment(label: dict) -> dict:
    """The model's row for a unit in Claudia's shape; ids are unit ids. A label_v2 row (string
    subject_scope, is_risk_heading, entity_mentions) is read too."""
    scope = label.get("subject_scope")
    return {"id": label["unit_id"], "label": label["label"],
            "context_ids": list(label["context_unit_ids"]),
            **{k: label.get(k, v) for k, v in LABEL_DEFAULTS.items()},
            "subject_scope": scope if isinstance(scope, list) else ([scope] if scope else []),
            "entity_mentions": label.get("entity_mentions") or []}


def review_entry(unit: dict, result: dict, source: dict) -> dict | None:
    """Per-unit decision: held (needs_review), retained with rule flags, or None (clean).
    Entity flags come later: step 8b extracts the mentions and step 9 checks them."""
    if result["label"] == "not_a_claim":
        return None
    ids = {"unit_id": unit["unit_id"]}
    fragments = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b in unit["raw_spans"]]
    if result["label"] == "needs_review":
        return {**ids, "assessment": result, "source_fragments": fragments, "reason": HELD_UNCERTAIN_REASON}
    rule_flags = source_review.claim_review_flags(unit, result)
    if rule_flags:
        return {**ids, "candidate_retained": True, "reason": RETAINED_REASON, "claim_review_flags": rule_flags}
    return None


def review(units: dict[str, dict], labels: list[dict]) -> list[dict]:
    from .label import source_index     # imported here: the label package imports this module
    sources, _ = source_index()
    vocabulary = source_review.source_vocabulary(sources)
    (data_dir() / "output").mkdir(parents=True, exist_ok=True)
    (data_dir() / "output" / "provisional_segment_definitions.json").write_text(
        json.dumps(vocabulary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    queue = []
    for label in labels:
        unit = units.get(label["unit_id"])
        if unit is None or label.get("source", "").startswith("rule:"):
            continue
        key = (unit["accession_number"], unit["section_key"])
        entry = review_entry(unit, assessment(label), sources[key])
        if entry:
            queue.append(entry)
    return queue


# ---------- manual decisions ----------

def overrides_path() -> Path:
    return Path(os.environ.get("PIPELINE_LABEL_OVERRIDES") or CONFIG_DIR / "label_overrides.csv")


def read_overrides() -> dict[str, dict]:
    """config/label_overrides.csv: unit_id, label, reviewer, note. A person's label for a unit
    wins over the model's and the prefilter's: release a not_a_claim or a held needs_review unit
    as a Claim (stated_fact, forward_looking, hedged_risk), or drop a Claim (not_a_claim).
    Optional column context_unit_ids (unit ids separated by ";") replaces the unit's context ids,
    e.g. the model's own context for a unit code held for its context ids; empty keeps them."""
    path = overrides_path()
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("unit_id") or "").strip()]
    out = {}
    for r in rows:
        label = (r.get("label") or "").strip()
        if label not in CLAIM_TYPES + ("not_a_claim",):
            raise SystemExit(f"{path}: {r['unit_id']}: label must be one of "
                             f"{', '.join(CLAIM_TYPES + ('not_a_claim',))}, not {label!r}")
        context = (r.get("context_unit_ids") or "").strip()
        out[r["unit_id"].strip()] = {"label": label, "reviewer": (r.get("reviewer") or "").strip() or None,
                                     "note": (r.get("note") or "").strip() or None,
                                     "context_unit_ids": [c.strip() for c in context.split(";") if c.strip()]
                                     if context else None}
    return out


def reviewed_labels(labels: list[dict] | None = None, unit_ids=None) -> list[dict]:
    """labels.jsonl with label_overrides.csv applied: what steps 8, 8b and 9 read. An overridden
    row keeps the model's label and source (model_label, model_source). An override for a unit
    with no label at all (its batch's reply was rejected, stop_on_failure: false) adds a manual
    row with no model label, so a person can fill that gap; only for ids in unit_ids (the units
    of this run), so an override for a unit that no longer exists adds nothing (validate reports
    it as an unknown unit)."""
    if labels is None:
        labels = list(read_jsonl(Paths.labels())) if Paths.labels().exists() else []
    overrides = read_overrides()
    if not overrides:
        return labels
    out = []
    for l in labels:
        o = overrides.get(l["unit_id"])
        if o is None or (o["label"] == l["label"] and o["context_unit_ids"] is None):
            out.append(l)
            continue
        context = {} if o["context_unit_ids"] is None else {"context_unit_ids": o["context_unit_ids"]}
        out.append(dict(l, label=o["label"], source="manual", model_label=l["label"],
                        model_source=l.get("source"), reviewer=o["reviewer"], override_note=o["note"], **context))
    labelled = {l["unit_id"] for l in labels}
    out += [{"unit_id": uid, "label": o["label"], "source": "manual", **LABEL_DEFAULTS,
             "context_unit_ids": o["context_unit_ids"] or [],
             "model_label": None, "model_source": None, "reviewer": o["reviewer"], "override_note": o["note"]}
            for uid, o in overrides.items() if uid not in labelled and unit_ids is not None and uid in unit_ids]
    return out


def write_not_a_claim_review(units: dict[str, dict], labels: list[dict]) -> int:
    """checks/validate/not_a_claim.csv: every unit the model labelled not_a_claim, to choose which
    to release (copy its unit_id into config/label_overrides.csv with a Claim type)."""
    rows = [{"unit_id": l["unit_id"], "ticker": units[l["unit_id"]]["ticker"],
             "accession_number": units[l["unit_id"]]["accession_number"],
             "section_path": units[l["unit_id"]]["section_path"], "text": units[l["unit_id"]]["text"],
             "annotation_note": l.get("annotation_note"), "source": l.get("source")}
            for l in labels if l["label"] == "not_a_claim" and not (l.get("source") or "").startswith("rule:")
            and l["unit_id"] in units]
    path = checks_dir("validate") / "not_a_claim.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["unit_id", "ticker", "accession_number", "section_path", "text",
                                          "annotation_note", "source"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def held_ids() -> set[str]:
    """Unit ids held from Claims by the last review (used by step 9)."""
    if not Paths.review_queue().exists():
        return set()
    return {r["unit_id"] for r in read_jsonl(Paths.review_queue()) if not r.get("candidate_retained")}


def run() -> int:
    units = {u["unit_id"]: u for u in read_jsonl(Paths.sentences())}
    model_labels = list(read_jsonl(Paths.labels())) if Paths.labels().exists() else []
    labels = reviewed_labels(model_labels, units)
    errors = [{"unit_id": uid, "error": f"{overrides_path().name}: unknown unit"}
              for uid in read_overrides() if uid not in units]

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

    manual = [l for l in labels if l.get("source") == "manual"]
    if manual:
        log(STEP, f"{len(manual)} labels set by a person ({overrides_path().name}): "
                  + ", ".join(f"{a}->{b}={n}" for (a, b), n in
                              Counter((l["model_label"], l["label"]) for l in manual).most_common()))
    n = write_not_a_claim_review(units, labels)
    log(STEP, f"{n} units the model labelled not_a_claim -> {checks_dir('validate') / 'not_a_claim.csv'}")

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
