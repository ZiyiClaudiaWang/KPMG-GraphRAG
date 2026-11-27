"""Step 9 - Build Claims: one candidate record per Claim occurrence, in Claudia's format.

Input : data/output/sentences.jsonl, data/output/labels.jsonl, data/output/review_queue.jsonl,
        data/sections/*.jsonl, data/interim/clean/*.jsonl, data/entity_catalog.json (optional, step 6b)
Output: data/output/claims.jsonl   one line per occurrence: a Claim sentence at one place in a section

Follows her build_candidates() field by field. Units labelled not_a_claim or needs_review
(held in step 8) are left out. claim_review_flags: step 8's rule flags (a Claim, flagged only).

quote: the unit's source text, every fragment joined by one space, so a multi-span unit (a list
group, or a sentence joined across a page break) is never cut to its first fragment.
multi_fragment is true for such a unit: its quote is not one continuous string of the source,
so a UI or check must use source_fragments (each piece's start, end and text), not search
for the quote.

Identity (#45, README "Unit ID & Claim ID"):
  claim_id                 the unit's unit_id: uuid5 of "<accession>|<section_key>|<raw_spans>".
                           A Claim is a statement at one place: the same sentence twice in one
                           section is two Claims. A cleaning change that keeps the spans keeps
                           the id; one that moves a unit's start, end or fragments changes it.
  claim_identity_version   source_span_position_v1 (was exact_source_sentence_v1, keyed by text)
  candidate_occurrence_id  sha256 of [claim_id, section content hash, raw_spans].
Every id in a record that points at another unit (context, risk category, lead sentence)
is a unit_id.

Each run compares its claim_ids with the previous claims.jsonl and writes the counts to
data/output/claim_id_report.json (Adeline's suggestion on #45): ids added, ids gone, and ids
kept whose quote changed.

The ontology fields (decision 2.3) are claim_id, quote, claim_type, accession_number,
section_id, extraction_status, context, subject_scope and annotation_note. Everything else
is provenance or review information.

Lead sentences come from step 4 (sentences.jsonl), in every section:
  is_lead_sentence         true if the sentence sums up the sentences under it (step 4 lead_source)
  provenance.lead_source   html, html_runin or rule for a lead, else null
  provenance.lead_unit_id  the lead above the sentence (step 4), or null. A unit_id, only
                           recorded: the lead itself may not be a Claim (not_a_claim, held).
Risk fields, by unit id: step 5 (data/interim/risk_assignments.jsonl) gives the heading fields and
risk_factor_id; step 5b's answers (data/interim/risk_themes.jsonl) are put on them here
(risk.themes.apply()):
  risk_factor_id           the lead's unit_id of the risk factor (one SEC risk) the sentence is
                           in, or null; a unit_id. Can differ from lead_unit_id: a risk factor
                           also covers the sub-headings below its lead (risk/factors.py)
  risk_category_text       the filing's own level-1 Item 1A heading above it ("Business Risks")
  risk_category_unit_id    that heading's unit_id, or null outside a category
  heading_risk_group       the groups that heading names (config/risk_taxonomy/risk_category_map.csv)
  risk_theme_status        classified | unknown | not_classified | no_risk_factor | risk_summary
                           (null outside Item 1A); see risk/themes.py
  risk_themes              the themes from step 5b, each {theme_id, rank, scope, source,
                           agrees_with_heading, model, taxonomy_version}: one HAS_RISK_THEME edge each
  risk_summary             true if it is in the risk factor summary at the top of Item 1A (step 4)
Step 7 (label_v3) fields: subject_scope (1 to 3 of validate.SUBJECT_SCOPES, main first),
is_boilerplate, time_reference (words of the sentence, or null).
Step 8b (extract.py, data/output/extractions.jsonl) fields, empty without its row:
  entity_mentions          (after matching) the segment / subsidiary / unknown mentions
  typed_mentions           organization, product, geography, regulation, government, person mentions
                           [{mention_text, entity_type, role, reference_kind, evidence_quote}]
  metrics                  [{metric_text, value_text, value, unit, period_text, direction}]
  event_type, affected_object, extraction_source (llm | llm_reused | null)
A label_v2 file (step 7 with entity_mentions) without extractions.jsonl still builds as before.
Entities named in a heading above the Claim (step 6b, entities/headings.py):
  heading_entity_links     [{entity_id, entity_type, mention_text, match_status, heading_unit_id,
                           heading_text}], matched / matched_auto only, leaving out entities the
                           sentence itself links; step 10: MENTIONS with found_by ["heading"]
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from . import source_review
from .entities import annotation as entity_annotation
from .common import UNIT_IDENTITY_VERSION, Paths, log, read_jsonl, write_jsonl

from .validate import CLAIM_TYPES, assessment, held_ids

RISK_FIELDS = ("risk_factor_id", "risk_category_text", "risk_category_unit_id", "heading_risk_group",
               "risk_theme_status", "risk_themes")
NO_RISK = {"risk_factor_id": None, "risk_category_text": None, "risk_category_unit_id": None,
           "heading_risk_group": [], "risk_theme_status": None, "risk_themes": []}

STEP = "build_claims"

POSTCHECK_POLICY_VERSION = "batch_review_v3"


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


EXTRACT_FIELDS = ("metrics", "event_type", "affected_object")


def split_mentions(extraction: dict | None, result: dict) -> tuple[list[dict], list[dict]]:
    """(catalog mentions: segment, subsidiary, unknown, matched in annotate_mentions; other typed
    mentions: organization, product, geography, ...). Without a step 8b row, step 7's mentions
    (label_v2) are the catalog mentions."""
    from .extract import CATALOG_TYPES
    if extraction is None:
        return result["entity_mentions"], []
    mentions = extraction["entity_mentions"]
    return ([m for m in mentions if m["entity_type"] in CATALOG_TYPES],
            [m for m in mentions if m["entity_type"] not in CATALOG_TYPES])


def candidate(unit: dict, label: dict, units: dict[str, dict], source: dict, cleaned: dict,
              vocabulary: list[dict], catalog: list[dict], risk: dict = NO_RISK,
              extraction: dict | None = None) -> dict:
    """Claudia's candidate record for one occurrence; extraction is the unit's step 8b row."""
    result = assessment(label)
    catalog_mentions, typed_mentions = split_mentions(extraction, result)
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
        "is_boilerplate": result["is_boilerplate"], "time_reference": result["time_reference"],
        "typed_mentions": typed_mentions,
        **{k: (extraction or {}).get(k, [] if k != "event_type" else None) for k in EXTRACT_FIELDS},
        "extraction_source": (extraction or {}).get("source"),
        "section_type": unit["section_type"],
        **{k: risk.get(k, NO_RISK[k]) for k in RISK_FIELDS},
        "is_lead_sentence": bool(unit.get("lead_source")), "risk_summary": unit.get("risk_summary", False),
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
                       "lead_unit_id": unit.get("lead_unit_id"), "lead_source": unit.get("lead_source"),
                       "request_hash": label["request_hash"],
                       "runner_version": label.get("runner_version") or label_runner_version(),
                       "model": label["model"], "prompt_version": label["prompt_version"]},
    }
    record.update(entity_annotation.annotate_mentions(unit["text"], catalog_mentions,
                                                      issuer_cik=source["cik"],
                                                      accession=source["accession_number"], catalog=catalog))
    reviewed = source_review.entity_review(unit, source, catalog_mentions, vocabulary)
    # related_*_ids keep only matched / matched_auto links (entities/annotation.py, #91 P1);
    # anything weaker stays a candidate on the mention. Step 10 does not load them yet (#91 P3).
    record.update(proposed_entity_mentions=reviewed["proposed_entity_mentions"], entity_review=reviewed,
                  postcheck_policy_version=POSTCHECK_POLICY_VERSION)
    record["entity_review_flags"] = sorted(set(record["entity_review_flags"] + reviewed["review_flags"]))
    record["claim_review_flags"] = source_review.claim_review_flags(unit, result)
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


def risk_assignments() -> list[dict]:
    """Step 5's Item 1A rows with step 5b's themes put on them; empty when step 5 has not run."""
    from .risk import factors as risk_factors, themes as risk_themes
    if not Paths.risk_assignments().exists():
        return []
    return risk_themes.apply(list(read_jsonl(Paths.risk_assignments())), risk_factors.load_factors(),
                             risk_themes.load_results())


def label_runner_version() -> str:
    from .label import RUNNER_VERSION       # imported here: the label package imports validate, which this imports
    return RUNNER_VERSION


def run() -> None:
    from .label import entity_catalog, source_index
    sources, cleaned = source_index()
    vocabulary = source_review.source_vocabulary(sources)
    catalog = entity_catalog()
    units = {u["unit_id"]: u for u in read_jsonl(Paths.sentences())}
    held = held_ids()
    risks = {a["unit_id"]: a for a in risk_assignments()}
    from .entities import headings
    under_headings = headings.inherited(list(units.values()), headings.read_rows())
    extractions = {r["unit_id"]: r for r in read_jsonl(Paths.extractions())} if Paths.extractions().exists() else {}

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
        record = candidate(unit, label, units, sources[key], cleaned[key], vocabulary, catalog,
                           risks.get(unit["unit_id"], NO_RISK), extractions.get(unit["unit_id"]))
        if extractions and unit["unit_id"] not in extractions:
            skipped["no step 8b extraction yet (built without mentions)"] += 1
        direct = set(record["related_segment_ids"]) | set(record["related_subsidiary_ids"])
        record["heading_entity_links"] = [l for l in under_headings.get(unit["unit_id"], [])
                                          if l["entity_id"] not in direct]
        records.append(record)

    report = id_report(records)
    write_jsonl(Paths.claims(), records)
    by_type = Counter(r["claim_type"] for r in records)
    log(STEP, f"{len(records)} occurrences of {len({r['claim_id'] for r in records})} Claims "
              f"({', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}); skipped {dict(skipped)} "
              f"-> {Paths.claims()}")
    log(STEP, f"{sum(r['is_lead_sentence'] for r in records)} occurrences are lead sentences, "
              f"{sum(bool(r['provenance']['lead_unit_id']) for r in records)} sit under one; "
              f"{sum(bool(r['risk_themes']) for r in records)} have a risk theme; "
              f"{sum(r['multi_fragment'] for r in records)} are multi-fragment (list groups, page breaks); "
              f"{sum(bool(r['heading_entity_links']) for r in records)} link an entity named in a heading above")
    log(STEP, f"claim_ids since the last build: {report['added']} added, {report['gone']} gone, "
              f"{report['kept']} kept ({report['quote_changed']} with a changed quote)"
              + (f"; previous build used {report['previous_identity_versions']}"
                 if report["previous_identity_versions"] != [UNIT_IDENTITY_VERSION] and report["previous"] else "")
              + f" -> {Paths.claim_id_report()}")


if __name__ == "__main__":
    run()
