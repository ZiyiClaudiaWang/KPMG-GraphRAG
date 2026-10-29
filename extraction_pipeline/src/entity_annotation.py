"""Entity mentions: segments and subsidiaries named in a Claim (Claudia's module).

The LLM returns mentions in step 5 (instructions are in prompts/label_v2.txt);
step 7b checks them against the sentence and matches them to the entity catalog
(data/entity_catalog.json). Catalog IDs are assigned by code only, and nothing
here writes to the graph. A product, location or competitor is never promoted
to a subsidiary from a name match alone.
"""
import re

VERSION = "entity_mentions_v1"
SCHEMA = {
    "type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["mention_text", "entity_type", "reference_kind", "evidence_quote"],
        "properties": {
            "mention_text": {"type": "string"},
            "entity_type": {"type": "string", "enum": ["segment", "subsidiary", "unknown"]},
            "reference_kind": {"type": "string", "enum": ["named", "generic", "uncertain"]},
            "evidence_quote": {"type": "string"},
        },
    },
}



def normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def validate_catalog(catalog):
    if not isinstance(catalog, list):
        raise ValueError("Entity catalog must be a list.")
    seen = set()
    for row in catalog:
        required = {"entity_id", "entity_type", "issuer_cik", "name", "aliases", "accessions", "review_status"}
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("Incomplete catalog row.")
        if row["entity_type"] not in ("segment", "subsidiary") or row["review_status"] not in ("reviewed", "needs_review"):
            raise ValueError("Invalid catalog type/status.")
        if not isinstance(row["issuer_cik"], str) or not re.fullmatch(r"\d{10}", row["issuer_cik"]):
            raise ValueError("Catalog issuer CIK must be ten digits.")
        if not all(isinstance(row[k], str) and row[k].strip() for k in ("entity_id", "name")):
            raise ValueError("Catalog ID and name must be nonempty.")
        for key in ("aliases", "accessions"):
            if not isinstance(row[key], list) or not all(isinstance(v, str) and v.strip() for v in row[key]):
                raise ValueError("Catalog aliases/accessions must be lists of nonempty strings.")
        if not row["accessions"]:
            raise ValueError("Catalog entries require an explicit filing scope.")
        identity = (row["issuer_cik"], row["entity_type"], row["entity_id"])
        if identity in seen:
            raise ValueError("Duplicate catalog identity.")
        seen.add(identity)


def annotate_mentions(text, mentions, *, issuer_cik, accession, catalog):
    validate_catalog(catalog)
    output, flags, related = [], [], {"segment": [], "subsidiary": []}
    for mention in mentions:
        m = dict(mention)
        m["matched_entity_id"] = None
        m["candidate_entity_ids"] = []
        wording, evidence = m["mention_text"], m["evidence_quote"]
        valid_source = bool(wording.strip() and evidence.strip() and wording in evidence and evidence in text)
        if not valid_source:
            m["match_status"] = "invalid_source_mention"
            flags.append("entity_mention_does_not_match_target_text")
        elif m["reference_kind"] == "generic":
            m["match_status"] = "generic_reference"
        elif m["reference_kind"] == "uncertain" or m["entity_type"] == "unknown":
            m["match_status"] = "needs_review"
        else:
            kind = m["entity_type"]
            cue = r"\bsegments?\b" if kind == "segment" else r"\bsubsidiar(?:y|ies)\b|\bwholly[- ]owned\b"
            eligible = [r for r in catalog if r["issuer_cik"] == issuer_cik and r["entity_type"] == kind and accession in r["accessions"]]
            hits = [r for r in eligible if normalized(wording) in {normalized(n) for n in [r["name"]] + r["aliases"]}]
            m["candidate_entity_ids"] = [r["entity_id"] for r in hits]
            if not re.search(cue, evidence, re.I):
                m["match_status"] = "needs_type_evidence_review"
            elif not eligible:
                m["match_status"] = "needs_catalog"
            elif not hits:
                m["match_status"] = "no_catalog_match"
            elif len(hits) > 1:
                m["match_status"] = "ambiguous_catalog_match"
            elif hits[0]["review_status"] != "reviewed":
                m["match_status"] = "candidate_match_needs_review"
            else:
                m["match_status"] = "matched"
                m["matched_entity_id"] = hits[0]["entity_id"]
                if hits[0]["entity_id"] not in related[kind]:
                    related[kind].append(hits[0]["entity_id"])
        output.append(m)
    return {"entity_mentions": output, "related_segment_ids": related["segment"],
            "related_subsidiary_ids": related["subsidiary"], "entity_review_flags": flags,
            "entity_annotation_version": VERSION, "graph_relations_created": 0}
