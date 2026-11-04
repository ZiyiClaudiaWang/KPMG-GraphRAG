"""Step 7b - Build Claims: one candidate record per Claim occurrence, in Claudia's format.

Input : data/output/sentences.jsonl, data/output/labels.jsonl, data/output/review_queue.jsonl,
        data/sections/*.jsonl, data/interim/clean/*.jsonl, data/entity_catalog.json (optional)
Output: data/output/claims.jsonl   one line per occurrence: a Claim sentence at one place in a section

Follows her build_candidates() field by field. Units labelled not_a_claim or needs_review,
and units held in step 6, are left out.

quote: the unit's source text, every fragment joined by one space, so a multi-span unit (a list
group, or a sentence joined across a page break) is never cut to its first fragment.
multi_fragment is true for such a unit: its quote is not one continuous string of the source,
so a UI or check must use source_fragments (each piece's start, end and text), not search
for the quote.

Identity (#45, README "Unit identity"):
  claim_id                 the unit's unit_id: uuid5 of "<accession>|<section_key>|<raw_spans>".
                           A Claim is a statement at one place: the same sentence twice in one
                           section is two Claims. A cleaning change that keeps the spans keeps
                           the id; one that moves a unit's start, end or fragments changes it.
  claim_identity_version   source_span_position_v1 (was exact_source_sentence_v1, keyed by text)
  candidate_occurrence_id  sha256 of [claim_id, section content hash, raw_spans].
Every id in a record that points at another unit (context, risk category, risk lead
sentence) is a unit_id.

Each run compares its claim_ids with the previous claims.jsonl and writes the counts to
data/output/claim_id_report.json (Adeline's suggestion on #45): ids added, ids gone, and ids
kept whose quote changed.

The ontology fields (decision 2.3) are claim_id, quote, claim_type, accession_number,
section_id, extraction_status, context, subject_scope and annotation_note. Everything else
is provenance or review information.

Risk fields come from step 7a (data/interim/risk_assignments.jsonl), by unit id:
  risk_ids               risk themes of the risk category the sentence sits under (may be empty)
  risk_category_id       that category heading's unit_id, or null outside a category
  risk_lead_sentence     true if the sentence opens and sums up one risk
  risk_summary           true if it is in the risk factor summary at the top of Item 1A
The risk lead sentence above each sentence is kept in provenance (risk_lead_unit_id).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from . import entity_annotation, source_review
from .common import UNIT_IDENTITY_VERSION, Paths, log, read_jsonl, write_jsonl

NO_RISK = {"risk_category_id": None, "risk_ids": [], "risk_lead_sentence": False, "risk_lead_unit_id": None}
from .validate import CLAIM_TYPES, assessment, held_ids

STEP = "build_claims"

POSTCHECK_POLICY_VERSION = "batch_review_v3"


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidate(unit: dict, label: dict, units: dict[str, dict], source: dict, cleaned: dict,
              vocabulary: list[dict], catalog: list[dict], risk: dict = NO_RISK) -> dict:
    """Claudia's candidate record for one occurrence."""
    result = assessment(label)
    spans = unit["raw_spans"]
    fragments = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b in spans]
    paragraph = cleaned["paragraphs"][unit["paragraph_index"]]
    parent = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b, _ in paragraph["pieces"]]
    context = [{k: units[c][k] for k in ("unit_id", "raw_spans", "text")} for c in result["context_ids"]]
    record = {
        "claim_id": unit["unit_id"],
        "claim_identity_version": UNIT_IDENTITY_VERSION,
        "quote": " ".join(f["quote"] for f in fragments), "multi_fragment": len(spans) > 1,
        "claim_type": result["label"],
        "accession_number": unit["accession_number"], "section_id": unit["section_id"],
        "section_logical_key": f"{unit['accession_number']}/{unit['section_key']}",
        "extraction_status": "needs_review", "context": " ".join(c["text"] for c in context) or None,
        "context_evidence": context,
        "subject_scope": result["subject_scope"], "annotation_note": result["annotation_note"],
        "section_type": unit["section_type"],
        "risk_ids": risk["risk_ids"], "risk_category_id": risk["risk_category_id"],
        "risk_lead_sentence": risk["risk_lead_sentence"], "risk_summary": unit.get("risk_summary", False),
        "source_fragments": fragments, "parent_paragraph_context": parent,
        "provenance": {"section_key": unit["section_key"],
                       "section_path": unit["section_path"], "source_locator": source["source_locator"],
                       "processing_set_id": source["processing_set_id"],
                       "processing_family": source.get("processing_family"),
                       "report_id": source.get("report_id"), "source_url": source.get("source_url"),
                       "cik": source.get("cik"), "reporting_period_end": source.get("reporting_period_end"),
                       "upstream_source_sha256": source.get("source_sha256"),
                       "upstream_extraction_version": source.get("extraction_version"),
                       "section_content_sha256": digest(source["content"]),
                       "coordinate_system": "exact_data_section_content_unicode_codepoints",
                       "raw_spans": spans, "context_unit_ids": [c["unit_id"] for c in context],
                       "risk_lead_unit_id": risk["risk_lead_unit_id"],
                       "request_hash": label["request_hash"],
                       "runner_version": label.get("runner_version") or label_runner_version(),
                       "model": label["model"], "prompt_version": label["prompt_version"]},
    }
    record.update(entity_annotation.annotate_mentions(unit["text"], result["entity_mentions"],
                                                      issuer_cik=source["cik"],
                                                      accession=source["accession_number"], catalog=catalog))
    reviewed = source_review.entity_review(unit, source, result["entity_mentions"], vocabulary)
    record.update(proposed_entity_mentions=reviewed["proposed_entity_mentions"], entity_review=reviewed,
                  postcheck_policy_version=POSTCHECK_POLICY_VERSION, related_segment_ids=[],
                  related_subsidiary_ids=[])
    for m in record["entity_mentions"]:        # no confirmed entity links in this unreviewed run
        m["matched_entity_id"] = None
    record["entity_review_flags"] = sorted(set(record["entity_review_flags"] + reviewed["review_flags"]))
    record["candidate_occurrence_id"] = digest(canonical([record["claim_id"],
                                                          record["provenance"]["section_content_sha256"],
                                                          spans]))
    return record


def id_report(records: list[dict], examples: int = 20) -> dict:
    """Compare these claim_ids with the claims.jsonl about to be replaced, and save the counts.

    A changed id is not an error, but it should never go unnoticed: a join on claim_id (Eval's
    citations, a review decision) silently stops matching for every id that changed.
    """
    old = {r["claim_id"]: r for r in read_jsonl(Paths.claims())} if Paths.claims().exists() else {}
    new = {r["claim_id"]: r for r in records}
    kept = [i for i in new if i in old]
    changed = [i for i in kept if new[i]["quote"] != old[i]["quote"]]
    report = {
        "identity_version": UNIT_IDENTITY_VERSION,
        "previous_identity_versions": sorted({r.get("claim_identity_version") or "" for r in old.values()}),
        "previous": len(old), "current": len(new),
        "added": sum(i not in old for i in new), "gone": sum(i not in new for i in old),
        "kept": len(kept), "quote_changed": len(changed),
        "examples": {
            "added": [new[i]["quote"][:120] for i in new if i not in old][:examples],
            "gone": [old[i]["quote"][:120] for i in old if i not in new][:examples],
            "quote_changed": [{"claim_id": i, "before": old[i]["quote"][:120], "after": new[i]["quote"][:120]}
                              for i in changed[:examples]],
        },
    }
    Paths.claim_id_report().parent.mkdir(parents=True, exist_ok=True)
    Paths.claim_id_report().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def label_runner_version() -> str:
    from .label import RUNNER_VERSION       # imported here: label.py imports validate, which this imports
    return RUNNER_VERSION


def run() -> None:
    from .label import entity_catalog, source_index
    sources, cleaned = source_index()
    vocabulary = source_review.source_vocabulary(sources)
    catalog = entity_catalog()
    units = {u["unit_id"]: u for u in read_jsonl(Paths.sentences())}
    held = held_ids()
    risks = {a["unit_id"]: a for a in read_jsonl(Paths.risk_assignments())} \
        if Paths.risk_assignments().exists() else {}

    records, skipped = [], Counter()
    for label in read_jsonl(Paths.labels()):
        unit = units[label["unit_id"]]
        if label["label"] not in CLAIM_TYPES:
            skipped[label["label"]] += 1
            continue
        if unit["unit_id"] in held:
            skipped["held for review"] += 1
            continue
        key = (unit["accession_number"], unit["section_key"])
        records.append(candidate(unit, label, units, sources[key], cleaned[key], vocabulary, catalog,
                                 risks.get(unit["unit_id"], NO_RISK)))

    report = id_report(records)
    write_jsonl(Paths.claims(), records)
    by_type = Counter(r["claim_type"] for r in records)
    log(STEP, f"{len(records)} occurrences of {len({r['claim_id'] for r in records})} Claims "
              f"({', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}); skipped {dict(skipped)} "
              f"-> {Paths.claims()}")
    log(STEP, f"{sum(bool(r['risk_ids']) for r in records)} occurrences have a risk theme; "
              f"{sum(r['multi_fragment'] for r in records)} are multi-fragment (list groups, page breaks)")
    log(STEP, f"claim_ids since the last build: {report['added']} added, {report['gone']} gone, "
              f"{report['kept']} kept ({report['quote_changed']} with a changed quote)"
              + (f"; previous build used {report['previous_identity_versions']}"
                 if report["previous_identity_versions"] != [UNIT_IDENTITY_VERSION] and report["previous"] else "")
              + f" -> {Paths.claim_id_report()}")


if __name__ == "__main__":
    run()
