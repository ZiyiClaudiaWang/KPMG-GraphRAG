"""Rule review after the LLM (Claudia's source_review.py, used by steps 5 to 7).

source_vocabulary  segment names defined in the Business sections. The two patterns are
                   specific to Apple and Microsoft (README, Open decisions, [Validate]).
claim_review_flags rules that hold a Claim for review (step 6).
entity_review      checks of the model's entity mentions against those names (steps 6 and 7).
The functions are hers, unchanged, so outputs match her run.
"""
import hashlib
import json
import re


def canonical(value): return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"))
def digest(text): return hashlib.sha256(text.encode("utf-8")).hexdigest()
def norm(text): return re.sub(r"\s+"," ",text).strip().casefold()


def source_vocabulary(sources):
    """Recognize only the two source-backed definition forms reviewed in this corpus.

    This is a provisional vocabulary, not the team's approved entity catalog.
    Every extracted name has an exact source span and a filing-specific scope.
    """
    rows=[]
    for (accession,section_key),source in sources.items():
        if source["section_type"]!="business": continue
        text=source["content"]
        definitions=[]
        if source["cik"]=="0000320193":
            for match in re.finditer(r"reportable segments consist of (?:the )?([^\n.]+)\.",text):
                start,end=match.span(1)
                for name in re.split(r",\s*|\s+and\s+",match.group(1)):
                    if name.strip():
                        name=name.strip();position=text.index(name,start,end)
                        definitions.append((name,position,position+len(name),match.start(),match.end()))
        if source["cik"]=="0000789019":
            for match in re.finditer(r"\bOur ([A-Z][^.\n]{0,100}?) segment consists of",text):
                name=match.group(1).strip();start,end=match.span(1)
                definitions.append((name,start,end,match.start(),match.end()))
        for name,start,end,evidence_start,evidence_end in definitions:
            candidate_id="candidate-segment-"+digest(canonical([source["cik"],accession,name]))[:20]
            rows.append({
                "candidate_entity_id":candidate_id,"entity_type":"segment","issuer_cik":source["cik"],
                "name":name,"accessions":[accession],"review_status":"needs_review",
                "matched_entity_id":None,"annotation_origin":"deterministic_source_definition_rule",
                "source": {"accession_number":accession,"section_key":section_key,"name_start":start,"name_end":end,
                           "name_quote":text[start:end],"definition_start":evidence_start,"definition_end":evidence_end,
                           "definition_quote":text[evidence_start:evidence_end],"source_locator":source["source_locator"],
                           "processing_set_id":source["processing_set_id"],"section_content_sha256":digest(text),
                           "coordinate_system":"exact_data_section_content_unicode_codepoints"},
            })
    # Repeated definitions in a filing remain one candidate with evidence retained.
    merged={}
    for row in rows:
        if row['candidate_entity_id'] in merged:
            merged[row['candidate_entity_id']].setdefault('additional_sources',[]).append(row['source'])
        else: merged[row['candidate_entity_id']]=row
    return list(merged.values())


def definition_mentions(unit,source,catalog):
    """Recover explicit segment-list or named-definition mentions, not arbitrary locations."""
    if unit['section_type']!='business':return []
    text=unit['text'];found=[]
    for entity in catalog:
        if entity['issuer_cik']!=source['cik'] or source['accession_number'] not in entity['accessions']:continue
        name=entity['name']
        if name not in text:continue
        defining=("reportable segments consist of" in text
                  or re.search(r"\bOur "+re.escape(name)+r" segment consists of",text))
        elaborating=bool(re.match(r"^(?:The\s+)?"+re.escape(name)+r"\s+includes\b",text))
        if not defining and not elaborating:continue
        found.append({"mention_text":name,"entity_type":"segment","reference_kind":"named",
                      "evidence_quote":text,"candidate_entity_id":entity['candidate_entity_id'],
                      "matched_entity_id":None,"match_status":"source_defined_candidate_needs_review",
                      "annotation_origin":"deterministic_source_definition_rule",
                      "type_definition_source":entity['source']})
    return found


def entity_review(unit,source,model_mentions,catalog):
    proposed=definition_mentions(unit,source,catalog)
    checked=[];flags=[]
    eligible=[e for e in catalog if e['issuer_cik']==source['cik'] and source['accession_number'] in e['accessions']]
    for mention in model_mentions:
        row=dict(mention);row['matched_entity_id']=None
        wording,evidence=mention['mention_text'],mention['evidence_quote']
        source_valid=bool(wording.strip() and evidence.strip() and wording in evidence and evidence in unit['text'])
        if not source_valid:
            row['review_status']='invalid_source_evidence';flags.append('model_entity_quote_not_in_target')
        elif norm(wording) in {'the company','company','we','our'}:
            row['review_status']='reporting_company_outside_segment_subsidiary_scope'
            flags.append('reporting_company_is_not_target_entity')
        elif mention['reference_kind']=='generic':
            row['review_status']='generic_reference_no_individual_id'
            if mention['entity_type']=='subsidiary' and re.search(r'\bsubsidiar(?:y|ies)\b',wording,re.I):
                proposed.append({**mention,'matched_entity_id':None,'match_status':'generic_reference',
                                 'annotation_origin':'saved_model_response'})
        elif mention['entity_type']=='segment':
            candidate_name=re.sub(r'\s+segment$','',wording,flags=re.I)
            matches=[e for e in eligible if norm(e['name'])==norm(candidate_name)]
            if not matches:
                row['review_status']='segment_type_not_supported_by_source_vocabulary'
                flags.append('product_or_business_line_may_be_mislabelled_as_segment')
            else:
                row['review_status']='consistent_with_source_defined_candidate'
                row['candidate_entity_ids']=[e['candidate_entity_id'] for e in matches]
        elif mention['entity_type']=='unknown' or mention['reference_kind']=='uncertain':
            row['review_status']='unresolved_type_not_a_confirmed_segment_or_subsidiary'
            flags.append('uncertain_entity_type')
        else:
            row['review_status']='needs_review_no_confirmed_entity_match'
            flags.append('named_subsidiary_requires_catalog_review')
        checked.append(row)
    # Check the explicit source-defined names, even when the model returned [].
    missing=[]
    for mention in proposed:
        if mention['entity_type']!='segment':continue
        name=mention['mention_text']
        if not any(norm(name)==norm(re.sub(r'\s+segment$','',m['mention_text'],flags=re.I)) for m in model_mentions):
            missing.append(name)
    if missing:flags.append('model_missed_source_defined_segment_names')
    return {"raw_model_mentions":model_mentions,"model_mention_review":checked,
            "proposed_entity_mentions":proposed,"model_missed_names":missing,
            "review_flags":sorted(set(flags)),"related_segment_ids":[],"related_subsidiary_ids":[],
            "note":"Provisional source/rule suggestions only; approved entity IDs and graph links remain unset."}


def claim_review_flags(unit,label,lookup):
    text=unit['text'];flags=[];intros=[]
    prefix=unit['sentence_id'].rsplit('|',1)[0]
    for context in label['context_ids']:
        sid=prefix+'|'+context
        if lookup[sid]['text'].rstrip().endswith(':'):
            intros.append(sid)
    if intros and label['label'] in {'stated_fact','hedged_risk','forward_looking'}:
        flags.append('list_intro_dependency_boundary_review')
    if label['label']=='hedged_risk' and re.search(r'\bis subject to\b.*\b(?:laws|regulations)\b',text,re.I) and not re.search(r'\b(?:may|might|could|can|would|if)\b',text,re.I):
        flags.append('existing_legal_condition_may_be_stated_fact')
    if re.search(r'\bhave passed\b.*\b(?:considering|pending)\b',text,re.I):
        flags.append('mixed_existing_and_prospective_statement_review')
    if re.search(r'\b(?:causes|has required)\b.*\bmay\b',text,re.I):
        flags.append('current_cost_or_past_action_with_possible_future_review')
    return flags,intros

