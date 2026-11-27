"""Step 7 targeted repair: one request for every unit whose id was missing or repeated."""

from __future__ import annotations

from copy import deepcopy

from .. import source_review
from ..llm.client import canonical, digest, parse_reply
from .requests import (batch_numbers, context_units, entity_catalog, label_fields, request_key,
                       response_schema, source_index, system_prompt)


REPAIR_VERSION = "target_repair_v2"
REPAIR_PROMPT = "repair_v2"          # prompts/repair_v2.txt: the label_v3 fields with repair instructions


def plan_repair(gap_ids: list[str], gaps: dict[str, str], units: list[dict], plans: list[dict], cfg: dict) -> dict:
    """One request for every unit whose id was missing or repeated (Claudia's targeted repair)."""
    sources, _ = source_index()
    lookup = {u["unit_id"]: u for u in units}
    item = deepcopy(response_schema()["schema"]["properties"]["results"]["items"])
    item["required"].remove("id")
    del item["properties"]["id"]
    targets, selected, contexts, schemas, payload = [], [], {}, {}, {}
    for i, uid in enumerate(gap_ids, 1):
        key, u = f"repair_{i}", lookup[uid]
        source = sources[(u["accession_number"], u["section_key"])]
        section = [r for r in units if r["accession_number"] == u["accession_number"]
                   and r["section_key"] == u["section_key"]]
        pos = next(j for j, r in enumerate(section) if r["unit_id"] == uid)
        prior = context_units(section, pos, [u], cfg)
        prior_numbers, _ = batch_numbers(prior, [])
        target_schema = deepcopy(item)
        target_schema["properties"]["context_ids"]["items"]["enum"] = prior_numbers
        targets.append({"unit_id": uid, "text": u["text"],
                        "section_content_sha256": digest(source["content"]), "raw_spans": u["raw_spans"],
                        "gap": gaps[uid]})
        selected.append(u)
        contexts[key] = prior
        schemas[key] = target_schema
        payload[key] = {"target_sentence": u["text"], "section": u["section_path"],
                        "context": [{"id": n, "text": r["text"]} for n, r in zip(prior_numbers, prior)]}
    schema = {"name": "target_gap_repair", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["repairs"], "properties": {
            "repairs": {"type": "object", "additionalProperties": False, "required": list(schemas),
                        "properties": schemas}}}}
    request = {"model": cfg["model"],
               "messages": [{"role": "system", "content": system_prompt(REPAIR_PROMPT)},
                            {"role": "user", "content": canonical(payload)}],
               "response_format": {"type": "json_schema", "json_schema": schema},
               "max_completion_tokens": cfg["max_completion_tokens"], "store": False}
    if cfg["model"].startswith(("gpt-4.1", "gpt-4o")):
        request["temperature"] = cfg["temperature"]
    identity = {"version": REPAIR_VERSION, "batch_request_hashes": [p["request_hash"] for p in plans],
                "request": request, "targets": targets, "selected_units": selected, "contexts": contexts,
                "source_metadata": [{k: v for k, v in sources[(u["accession_number"], u["section_key"])].items()
                                     if k != "content"} for u in selected],
                "entity_catalog": entity_catalog(),
                "provisional_segment_definitions": source_review.source_vocabulary(sources)}
    return {"batch": {"batch_id": "target_repair", "unit_ids": gap_ids}, "request": request,
            "identity": identity, "request_hash": digest(canonical(request_key(request, REPAIR_VERSION))),
            "contexts": contexts, "schema": schema, "texts": {u["unit_id"]: u["text"] for u in selected}}


def repair_labels(plan: dict, record: dict) -> tuple[list[str], list[dict]]:
    """Problems with the repair reply, or one label per target."""
    problems, parsed = parse_reply(record["raw_response"], plan["schema"]["schema"], "repairs",
                                   duplicate_keys_ok=False)
    if problems:
        return problems, []
    labels = []
    for i, uid in enumerate(plan["batch"]["unit_ids"], 1):
        key = f"repair_{i}"
        row = deepcopy(parsed["repairs"][key])
        context = {f"c{j}": r["unit_id"] for j, r in enumerate(plan["contexts"][key], 1)}
        order = {n: j for j, n in enumerate(context)}
        refs = row["context_ids"]
        if len(refs) != len(set(refs)) or any(r not in order for r in refs) \
                or [order[r] for r in refs] != sorted(order[r] for r in refs):
            return [f"{key}: invalid context ids; no repair labels published"], []
        labels.append({"unit_id": uid, "label": row["label"], "source": "llm_repair",
                       "context_unit_ids": [context[c] for c in refs],
                       **label_fields(row, plan["texts"].get(uid)),
                       "batch_id": "target_repair", "request_hash": plan["request_hash"],
                       "model": record["raw_response"].get("model"), "prompt_version": REPAIR_VERSION,
                       "runner_version": REPAIR_VERSION})
    return [], labels
