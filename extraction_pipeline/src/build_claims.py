"""Step 7b - Build Claims: one candidate record per Claim occurrence, in Claudia's format.

Input : data/output/sentences.jsonl, data/output/labels.jsonl, data/output/review_queue.jsonl,
        data/sections/*.jsonl, data/interim/clean/*.jsonl, data/entity_catalog.json (optional)
Output: data/output/claims.jsonl   one line per occurrence: a Claim sentence at one place in a section

Follows her build_candidates() field by field, so the same labels give the same records as
her candidate_occurrences.jsonl. Units labelled not_a_claim or needs_review, and units held
in step 6, are left out.

quote: the unit's source text, every fragment joined by one space, so a multi-span unit (a list
group, or a sentence joined across a page break) is never cut to its first fragment. Step 6
holds such units for review; source_fragments gives each piece's position.

Identity:
  claim_id                 uuid5 of "<accession>|<section_key>|<exact sentence text>". The same
                           sentence twice in one section shares a claim_id; in another filing it
                           is a different Claim.
  candidate_occurrence_id  sha256 of [claim_id, section content hash, raw_spans]: one per place.
Step 8 merges occurrences with the same claim_id into one Claim node.

The ontology fields (decision 2.3) are claim_id, quote, claim_type, accession_number,
section_id, extraction_status, context, subject_scope and annotation_note. Everything else
is provenance or review information.

Risk fields come from step 7a (data/interim/risk_assignments.jsonl), by sentence id:
  risk_ids               risk themes of the risk category the sentence sits under (may be empty)
  risk_category_id       that category heading's sentence_id, or null outside a category
  risk_lead_sentence     true if the sentence opens and sums up one risk
  risk_summary           true if it is in the risk factor summary at the top of Item 1A
The risk lead sentence above each sentence is kept in provenance (risk_lead_sentence_id).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter

from . import entity_annotation, source_review
from .common import Paths, log, read_jsonl, write_jsonl

NO_RISK = {"risk_category_id": None, "risk_ids": [], "risk_lead_sentence": False, "risk_lead_sentence_id": None}
from .validate import CLAIM_TYPES, assessment, held_ids

STEP = "build_claims"

# Fixed namespace so the same input always yields the same claim_id.
CLAIM_NAMESPACE = uuid.UUID("5f0c7e2a-6b1d-4c8e-9a3f-2d7b1e4c9a60")
POSTCHECK_POLICY_VERSION = "batch_review_v3"


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_id(accession: str, section_key: str, text: str) -> str:
    return str(uuid.uuid5(CLAIM_NAMESPACE, f"{accession}|{section_key}|{text}"))


def candidate(unit: dict, label: dict, units: dict[str, dict], source: dict, cleaned: dict,
              vocabulary: list[dict], catalog: list[dict], risk: dict = NO_RISK) -> dict:
    """Claudia's candidate record for one occurrence."""
    result = assessment(label)
    spans = unit["raw_spans"]
    fragments = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b in spans]
    paragraph = cleaned["paragraphs"][unit["paragraph_index"]]
    parent = [{"start": a, "end": b, "quote": source["content"][a:b]} for a, b, _ in paragraph["pieces"]]
    prefix = unit["sentence_id"].rsplit("|", 1)[0]
    context = [{"sentence_id": units[f"{prefix}|{c}"]["sentence_id"],
                "raw_spans": units[f"{prefix}|{c}"]["raw_spans"],
                "text": units[f"{prefix}|{c}"]["text"]} for c in result["context_ids"]]
    record = {
        "claim_id": claim_id(unit["accession_number"], unit["section_key"], unit["text"]),
        "claim_identity_version": "exact_source_sentence_v1",
        "quote": " ".join(f["quote"] for f in fragments), "claim_type": result["label"],
        "accession_number": unit["accession_number"], "section_id": unit["section_id"],
        "section_logical_key": f"{unit['accession_number']}/{unit['section_key']}",
        "extraction_status": "needs_review", "context": " ".join(c["text"] for c in context) or None,
        "context_evidence": context,
        "subject_scope": result["subject_scope"], "annotation_note": result["annotation_note"],
        "section_type": unit["section_type"],
        "risk_ids": risk["risk_ids"], "risk_category_id": risk["risk_category_id"],
        "risk_lead_sentence": risk["risk_lead_sentence"], "risk_summary": unit.get("risk_summary", False),
        "source_fragments": fragments, "parent_paragraph_context": parent,
        "provenance": {"sentence_ids": [unit["sentence_id"]], "section_key": unit["section_key"],
                       "section_path": unit["section_path"], "source_locator": source["source_locator"],
                       "processing_set_id": source["processing_set_id"],
                       "processing_family": source.get("processing_family"),
                       "report_id": source.get("report_id"), "source_url": source.get("source_url"),
                       "cik": source.get("cik"), "reporting_period_end": source.get("reporting_period_end"),
                       "upstream_source_sha256": source.get("source_sha256"),
                       "upstream_extraction_version": source.get("extraction_version"),
                       "section_content_sha256": digest(source["content"]),
                       "coordinate_system": "exact_data_section_content_unicode_codepoints",
                       "raw_spans": spans, "context_sentence_ids": result["context_ids"],
                       "risk_lead_sentence_id": risk["risk_lead_sentence_id"],
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


def label_runner_version() -> str:
    from .label import RUNNER_VERSION       # imported here: label.py imports validate, which this imports
    return RUNNER_VERSION


def run() -> None:
    from .label import entity_catalog, source_index
    sources, cleaned = source_index()
    vocabulary = source_review.source_vocabulary(sources)
    catalog = entity_catalog()
    units = {u["sentence_id"]: u for u in read_jsonl(Paths.sentences())}
    held = held_ids()
    risks = {a["sentence_id"]: a for a in read_jsonl(Paths.risk_assignments())} \
        if Paths.risk_assignments().exists() else {}

    records, skipped = [], Counter()
    for label in read_jsonl(Paths.labels()):
        unit = units[label["sentence_id"]]
        if label["label"] not in CLAIM_TYPES:
            skipped[label["label"]] += 1
            continue
        if unit["sentence_id"] in held:
            skipped["held for review"] += 1
            continue
        key = (unit["accession_number"], unit["section_key"])
        records.append(candidate(unit, label, units, sources[key], cleaned[key], vocabulary, catalog,
                                 risks.get(unit["sentence_id"], NO_RISK)))

    write_jsonl(Paths.claims(), records)
    by_type = Counter(r["claim_type"] for r in records)
    log(STEP, f"{len(records)} occurrences of {len({r['claim_id'] for r in records})} Claims "
              f"({', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}); skipped {dict(skipped)} "
              f"-> {Paths.claims()}")
    log(STEP, f"{sum(bool(r['risk_ids']) for r in records)} occurrences have a risk theme")


if __name__ == "__main__":
    run()
