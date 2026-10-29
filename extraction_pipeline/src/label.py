"""Step 5 - Label: the only step that calls the LLM (OpenAI).

Follows Claudia's checked_pilot.py, so the same input gives the same requests,
request hashes and cache folders as her run.

Input : data/output/sentences.jsonl, data/sections/*.jsonl, config/claim_types.yaml,
        prompts/<prompt_version>.txt, data/entity_catalog.json (optional)
Output: data/output/labels.jsonl         one label per unit (LLM, code review or prefilter rule)
        data/output/usage.jsonl          one usage record per API request
        data/interim/label_batches.jsonl the planned batches
        data/checked_pilot/<hash>/       one folder per batch request:
            request.json          what was sent, with the source versions it depends on
            attempt.json          written before sending; marks a request that may be charged
            raw_response.json     the reply, saved before any parsing
            usage.json            tokens and estimated cost
            unit_quarantine.json  only when single units of the reply were set aside
            validation_errors.json  only when the reply was rejected
        data/targeted_repair/<hash>/     the repair request, same files

Money rules (from her code):
- A request whose raw_response.json exists is never sent again.
- No automatic resend: the client does not retry, and if attempt.json exists without a
  raw_response.json (a crash or timeout after sending), the run stops for a person to check.
- The run stops before a request could take total estimated spend past budget_usd.

A reply is rejected, and publishes no labels, when it is incomplete (finish_reason is not
"stop"), a refusal, not exactly {"results": [...]}, or off the JSON schema. By default the
run then stops (stop_on_failure); max_retries > 0 sends a follow-up request listing the problems.

Problems with single units are handled as in Claudia's run (her recovery v2 and repair scripts):
- Unit quarantine: the rest of the reply is kept. A unit with invalid context ids, or whose
  id is missing or repeated, becomes needs_review (source "code_quarantine", no Claim). Rows
  with ids not in the batch are ignored. Nothing is guessed from row order.
- Targeted repair: once every batch has a reply, units whose id was missing or repeated are
  sent again in one request, each under a key assigned by code (repair_1, repair_2, ...),
  with its own 4 preceding units as context. Their labels replace the placeholders.

Usage:
  python -m src.label --dry-run                         plan batches, estimate cost, no API calls
  python -m src.label --filing 0000320193-25-000079     label one filing first
  python -m src.label --limit 3                         send at most 3 new requests
  python -m src.label --report                          print token and cost totals
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from . import entity_annotation, source_review
from .clean import ONE_FOR_ONE
from .common import (PROMPTS_DIR, Paths, append_jsonl, data_dir, filings, load_config, load_env, log,
                     read_jsonl, settings, write_jsonl)
from .validate import LABELS, check_reply

STEP = "label"
RUNNER_VERSION = "kelly_checked_openai_v3_postchecks"   # Claudia's runner id; part of every request hash


class StopRun(Exception):
    """Stop the run: budget reached, an uncertain earlier attempt, or a rejected reply."""


def canonical(value) -> str:
    """Compact, key-sorted JSON: the same value always gives the same text and hash."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------- sources ----------

def source_index() -> tuple[dict, dict]:
    """Data team sections and cleaned sections, keyed by (accession, section_key)."""
    sources, cleaned = {}, {}
    for f in filings():
        for row in read_jsonl(Paths.sections(f)):
            if row["accession_number"] != f["accession"] or row["cik"] != f["cik"] or row["form"] != f["form"]:
                raise ValueError("Section export identity differs from config/filings.yaml.")
            sources[(row["accession_number"], row["section_key"])] = row
        for row in read_jsonl(Paths.cleaned(f)):
            cleaned[(row["accession_number"], row["section_key"])] = row
    return sources, cleaned


def verify_units(units: list[dict], sources: dict) -> None:
    """Every unit must be rebuildable from the Data team content at its raw_spans, before anything is sent."""
    if len({u["sentence_id"] for u in units}) != len(units):
        raise ValueError("Duplicate sentence ids in sentences.jsonl.")
    for u in units:
        raw = sources[(u["accession_number"], u["section_key"])]
        for key in ("section_id", "processing_set_id", "source_locator", "form", "ticker"):
            if u[key] != raw[key]:
                raise ValueError(f"{u['sentence_id']}: {key} differs from the section export.")
        pieces, cursor = [], -1
        if not u["raw_spans"]:
            raise ValueError(f"{u['sentence_id']}: no raw_spans.")
        for start, end in u["raw_spans"]:
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw["content"]) \
                    or start < cursor:
                raise ValueError(f"{u['sentence_id']}: invalid or overlapping raw_spans.")
            pieces.append(raw["content"][start:end].translate(ONE_FOR_ONE))
            cursor = end
        if " ".join(pieces) != u["text"]:
            raise ValueError(f"{u['sentence_id']}: text cannot be rebuilt from the source.")


def entity_catalog() -> list[dict]:
    path = data_dir() / "entity_catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entity_annotation.validate_catalog(catalog)
    return catalog


# ---------- prompt, schema, request ----------

def local_id(sentence_id: str) -> str:
    """'0000320193-25-000079|part-i-item-1a|s0042' -> 's0042' (shorter ids, fewer tokens)."""
    return sentence_id.rsplit("|", 1)[1]


def system_prompt(prompt_version: str) -> str:
    """prompts/<version>.txt with the label definitions and rules from config/claim_types.yaml."""
    types = load_config("claim_types")
    definitions = [f"- {name}: {text}" for name, text in types["claim_types"].items()]
    definitions.append(f"- not_a_claim: {types['not_a_claim']}")
    rules = "\n".join(f"- {r}" for r in types["rules"])
    template = (PROMPTS_DIR / f"{prompt_version}.txt").read_text(encoding="utf-8")
    return template.format(label_definitions="\n".join(definitions), rules=rules)


def user_payload(batch: dict, units: dict[str, dict]) -> dict:
    return {
        "filing": batch["filing"],
        "section": units[batch["sentence_ids"][0]]["section_path"].split(" > ")[0],
        "context": [{"id": local_id(s), "text": units[s]["text"]} for s in batch["context_ids"]],
        "sentences": [{"id": local_id(s), "section_path": units[s]["section_path"], "text": units[s]["text"]}
                      for s in batch["sentence_ids"]],
    }


def response_schema() -> dict:
    result = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "label", "context_ids", "subject_scope", "annotation_note", "is_risk_heading",
                     "entity_mentions"],
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string", "enum": list(LABELS)},
            "context_ids": {"type": "array", "items": {"type": "string"}},
            "subject_scope": {"type": ["string", "null"]},
            "annotation_note": {"type": ["string", "null"]},
            "is_risk_heading": {"type": "boolean"},
            "entity_mentions": entity_annotation.SCHEMA,
        },
    }
    return {
        "name": "sentence_labels",
        "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": result}},
        },
    }


def build_request(batch: dict, units: dict[str, dict], cfg: dict, system: str) -> dict:
    request = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": canonical(user_payload(batch, units))}],
        "response_format": {"type": "json_schema", "json_schema": response_schema()},
        "max_completion_tokens": cfg["max_completion_tokens"],
        "store": False,                          # OpenAI does not keep the request
    }
    # Only these model families take temperature; newer reasoning models may reject it.
    if cfg["model"].startswith(("gpt-4.1", "gpt-4o")):
        request["temperature"] = cfg["temperature"]
    return request


# ---------- batches ----------

def plan_batches(units: list[dict], cfg: dict) -> list[dict]:
    """One plan per request: consecutive labelable units of a section, batch_max_sentences at a time.

    A plan holds the batch, the request, and the identity (request plus the source versions
    it depends on). request_hash = hash of the identity; it names the cache folder.
    A batch whose request exceeds max_request_bytes is halved until it fits.
    """
    sources, _ = source_index()
    verify_units(units, sources)
    system = system_prompt(cfg["prompt_version"])
    lookup = {u["sentence_id"]: u for u in units}
    vocabulary = source_review.source_vocabulary(sources)
    catalog = entity_catalog()

    by_section: dict[tuple, list[dict]] = defaultdict(list)
    for u in units:
        by_section[(u["accession_number"], u["section_key"])].append(u)

    plans = []
    for (accession, section_key), section_units in sorted(by_section.items()):
        source = sources[(accession, section_key)]
        eligible = [u for u in section_units if u["prefilter"] is None]
        start = 0
        while start < len(eligible):
            size = min(cfg["batch_max_sentences"], len(eligible) - start)
            while True:
                selected = eligible[start:start + size]
                first = next(i for i, u in enumerate(section_units) if u["sentence_id"] == selected[0]["sentence_id"])
                context = [u for u in section_units[:first] if u["role"] != "section_title"][-cfg["context_sentences"]:]
                batch = {
                    "batch_id": f"{accession}|{section_key}|checked_start_{start}_n{len(selected)}",
                    "accession_number": accession, "section_key": section_key,
                    "section_type": selected[0]["section_type"],
                    "filing": f"{source['ticker']} {source['form']}",
                    "sentence_ids": [u["sentence_id"] for u in selected],
                    "context_ids": [u["sentence_id"] for u in context],
                    "local_ids": [local_id(u["sentence_id"]) for u in selected],
                    "context_local_ids": [local_id(u["sentence_id"]) for u in context],
                }
                request = build_request(batch, lookup, cfg, system)
                if len(canonical(request).encode("utf-8")) <= cfg["max_request_bytes"] or size == 1:
                    break
                size = max(1, size // 2)
            identity = {
                "runner_version": RUNNER_VERSION, "request": request, "batch": batch,
                "source_section_sha256": digest(source["content"]),
                "processing_set_id": source["processing_set_id"],
                "source_locator": source["source_locator"],
                "selected_units": selected,
                "provisional_source_segment_definitions": vocabulary,
                "context_units": context,
                "source_metadata": {k: v for k, v in source.items() if k != "content"},
                "entity_catalog": catalog,
            }
            plans.append({"batch": batch, "request": request, "identity": identity,
                          "request_hash": digest(canonical(identity))})
            start += size
    return plans


# ---------- cost ----------

def usage_record(raw: dict, request_hash: str, latency_ms, request_id, batch: dict, cfg: dict) -> dict:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    p_in, p_cached, p_out = (cfg.get("price_input_per_1m"), cfg.get("price_cached_input_per_1m"),
                             cfg.get("price_output_per_1m"))
    estimate = None
    if None not in (p_in, p_cached, p_out) and all(type(v) is int for v in (inp, out, cached)) and 0 <= cached <= inp:
        estimate = ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_id": batch["batch_id"], "request_hash": request_hash,
        "response_id": raw.get("id"), "api_request_id": request_id, "model_returned": raw.get("model"),
        "usage_raw": usage, "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"), "generation_latency_ms": latency_ms,
        "estimated_cost_usd": estimate,
        "cost_status": "estimated_from_configured_rates" if estimate is not None else "unknown_price_or_usage",
    }


def reserve_cost(request: dict, cfg: dict) -> float:
    """Upper estimate of one request: its bytes as input tokens plus the full output allowance."""
    if cfg.get("price_input_per_1m") is None or cfg.get("price_output_per_1m") is None:
        return 0.0
    return ((len(canonical(request).encode("utf-8")) + 2048) * cfg["price_input_per_1m"]
            + request["max_completion_tokens"] * cfg["price_output_per_1m"]) / 1_000_000


# ---------- one request ----------

def no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON object key: " + key)
        out[key] = value
    return out


def parse_reply(raw: dict, schema: dict, envelope: str, duplicate_keys_ok: bool = True) -> tuple[list[str], dict]:
    """Hard problems with a raw reply (these reject it whole), and the parsed JSON when there are none."""
    from jsonschema import Draft202012Validator
    choices = raw.get("choices") or []
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        return ["Missing or incomplete completion; raw response retained."], {}
    msg = choices[0].get("message") or {}
    if msg.get("refusal"):
        return ["Model refusal; raw response retained."], {}
    try:
        hook = None if duplicate_keys_ok else no_duplicate_keys
        parsed = json.loads(msg.get("content") or "", object_pairs_hook=hook)
        if not isinstance(parsed, dict) or set(parsed) != {envelope}:
            raise ValueError("Unexpected response envelope.")
    except (ValueError, TypeError) as exc:
        return ["Invalid JSON response: " + type(exc).__name__], {}
    problems = ["Schema mismatch: " + e.message[:200] for e in Draft202012Validator(schema).iter_errors(parsed)]
    return problems, parsed


def context_flags(row: dict, order: dict[str, int]) -> list[str]:
    refs, flags = row["context_ids"], []
    if len(refs) != len(set(refs)):
        flags.append("duplicate_context_references")
    if any(c not in order for c in refs):
        flags.append("context_not_supplied")
    if any(c in order and order[c] >= order[row["id"]] for c in refs):
        flags.append("self_or_future_context_reference")
    if all(c in order for c in refs) and [order[c] for c in refs] != sorted(order[c] for c in refs):
        flags.append("context_not_in_source_order")
    return flags


def quarantine(batch: dict, results: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Claudia's project_targets(): one row per expected id; units with problems become needs_review.

    Returns (rows, held units, rows with ids not in the batch). No id is reassigned.
    """
    groups = defaultdict(list)
    for row in results:
        groups[row["id"]].append(row)
    expected = batch["local_ids"]
    order = {s: i for i, s in enumerate(batch["context_local_ids"] + expected)}
    rows, held = [], []
    for sid in expected:
        originals = groups[sid]
        flags = (["missing_model_target_id"] if not originals else
                 ["duplicate_model_target_id"] if len(originals) > 1 else context_flags(originals[0], order))
        if not flags:
            rows.append(deepcopy(originals[0]))
            continue
        if len(originals) == 1:
            row = deepcopy(originals[0])
            row["label"], row["context_ids"] = "needs_review", []
        else:
            row = {"id": sid, "label": "needs_review", "context_ids": [], "subject_scope": None,
                   "annotation_note": None, "is_risk_heading": False, "entity_mentions": []}
        row["annotation_note"] = ("CODE REVIEW ONLY: " + ", ".join(flags) + ". No candidate Claim published; "
                                  "original raw rows retained in unit_quarantine.json. "
                                  "This is not a model classification.")
        held.append({"sentence_id": f"{batch['accession_number']}|{batch['section_key']}|{sid}",
                     "review_flags": flags, "original_model_assessments": deepcopy(originals),
                     "processing_projection": deepcopy(row), "disposition": "held_for_review_no_candidate"})
        rows.append(row)
    extras = [deepcopy(r) for r in results if r["id"] not in set(expected)]
    return rows, held, extras


class Labeller:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = None              # created on the first real request
        self.spent = spent_so_far()

    def folder(self, request_hash: str, kind: str = "checked_pilot") -> Path:
        return data_dir() / kind / request_hash[:24]

    def cached(self, plan: dict, kind: str = "checked_pilot") -> dict | None:
        path = self.folder(plan["request_hash"], kind) / "raw_response.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def send(self, plan: dict, kind: str = "checked_pilot") -> dict:
        """Send one request once. Its raw reply is saved before anything else happens."""
        import openai  # imported here so the other steps and the tests run without the package
        folder, request_hash = self.folder(plan["request_hash"], kind), plan["request_hash"]
        if (folder / "attempt.json").exists():
            raise StopRun(f"{plan['batch']['batch_id']}: an earlier attempt has no saved reply and may have "
                          f"been charged. Check {folder / 'attempt.json'} before sending it again.")
        budget = self.cfg.get("budget_usd")
        if budget is not None and self.spent + reserve_cost(plan["request"], self.cfg) > budget:
            raise StopRun(f"budget of ${budget} reached (spent about ${self.spent:.4f}); cached work is kept")
        if self.client is None:
            load_env()
            self.client = openai.OpenAI(base_url=self.cfg["base_url"])
        save_json(folder / "request.json", plan["identity"])
        save_json(folder / "attempt.json", {"request_hash": request_hash, "status": "started",
                                            "started_at_utc": datetime.now(timezone.utc).isoformat(),
                                            "automatic_retries": 0, "generation_requests": 1})
        started = time.perf_counter()
        try:
            response = self.client.with_options(max_retries=0, timeout=120.0).chat.completions.create(
                **plan["request"])
        except Exception as exc:
            save_json(folder / "attempt.json", {"request_hash": request_hash, "status": "error_no_response",
                                                "error_type": type(exc).__name__, "automatic_retries": 0,
                                                "billing": "unknown", "generation_requests": 1})
            raise StopRun(f"{plan['batch']['batch_id']}: {type(exc).__name__}; not resent automatically") from exc
        record = {"request_hash": request_hash, "raw_response": response.model_dump(mode="json"),
                  "api_request_id": getattr(response, "_request_id", None),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
        save_json(folder / "raw_response.json", record)          # saved before any parsing
        usage = usage_record(record["raw_response"], request_hash, record["latency_ms"],
                             record["api_request_id"], plan["batch"], self.cfg)
        save_json(folder / "usage.json", usage)
        append_jsonl(Paths.usage(), usage)
        self.spent += usage["estimated_cost_usd"] or 0.0
        return record

    def label(self, plan: dict, send: bool) -> dict:
        """Reply for one batch: {record, problems, results, held, sent}; follows retries if set.

        results has one row per expected id once the reply is accepted; held lists the
        units set aside by quarantine().
        """
        attempt_plan, sent = plan, False
        out = {"record": None, "problems": [], "results": [], "held": [], "sent": False}
        for attempt in range(self.cfg["max_retries"] + 1):
            record = self.cached(attempt_plan)
            if record is None:
                if not send:
                    return dict(out, sent=sent)
                record = self.send(attempt_plan)
                sent = True
            folder = self.folder(attempt_plan["request_hash"])
            problems, parsed = parse_reply(record["raw_response"], response_schema()["schema"], "results")
            if not problems:
                results, held, extras = parsed["results"], [], []
                if check_reply(plan["batch"], results):
                    results, held, extras = quarantine(plan["batch"], results)
                    problems = check_reply(plan["batch"], results)      # must now pass
                if not problems:
                    (folder / "validation_errors.json").unlink(missing_ok=True)
                    if held or extras:
                        save_json(folder / "unit_quarantine.json", {
                            "request_hash": attempt_plan["request_hash"], "batch_id": plan["batch"]["batch_id"],
                            "quarantined_units": held, "unexpected_model_rows": extras})
                    return {"record": record, "problems": [], "results": results, "held": held, "sent": sent}
            save_json(folder / "validation_errors.json", problems)
            if attempt == self.cfg["max_retries"]:
                break
            content = record["raw_response"]["choices"][0]["message"].get("content") or "" \
                if record["raw_response"].get("choices") else ""
            request = deepcopy(attempt_plan["request"])
            request["messages"] += [{"role": "assistant", "content": content},
                                    {"role": "user", "content": "Your reply had these problems: "
                                     + "; ".join(problems) + ". Return the complete corrected result for every sentence."}]
            identity = {**attempt_plan["identity"], "request": request, "retry_of": attempt_plan["request_hash"]}
            attempt_plan = {**plan, "request": request, "identity": identity,
                            "request_hash": digest(canonical(identity))}
        return {"record": record, "problems": problems, "results": [], "held": [], "sent": sent}


# ---------- targeted repair ----------

REPAIR_VERSION = "target_repair_v1"
REPAIR_PROMPT = "repair_v1"          # prompts/repair_v1.txt: the batch prompt with repair instructions


def plan_repair(gap_ids: list[str], gaps: dict[str, str], units: list[dict], plans: list[dict], cfg: dict) -> dict:
    """One request for every unit whose id was missing or repeated (Claudia's targeted repair)."""
    sources, _ = source_index()
    lookup = {u["sentence_id"]: u for u in units}
    item = deepcopy(response_schema()["schema"]["properties"]["results"]["items"])
    item["required"].remove("id")
    del item["properties"]["id"]
    targets, selected, contexts, schemas, payload = [], [], {}, {}, {}
    for i, sid in enumerate(gap_ids, 1):
        key, u = f"repair_{i}", lookup[sid]
        source = sources[(u["accession_number"], u["section_key"])]
        section = [r for r in units if r["accession_number"] == u["accession_number"]
                   and r["section_key"] == u["section_key"]]
        pos = next(j for j, r in enumerate(section) if r["sentence_id"] == sid)
        prior = [r for r in section[:pos] if r["role"] != "section_title"][-cfg["context_sentences"]:]
        target_schema = deepcopy(item)
        target_schema["properties"]["context_ids"]["items"]["enum"] = [local_id(r["sentence_id"]) for r in prior]
        targets.append({"sentence_id": sid, "source_unit_key": u["source_unit_key"], "text": u["text"],
                        "section_content_sha256": digest(source["content"]), "raw_spans": u["raw_spans"],
                        "gap": gaps[sid]})
        selected.append(u)
        contexts[key] = prior
        schemas[key] = target_schema
        payload[key] = {"target_sentence": u["text"], "section": u["section_path"],
                        "context": [{"id": local_id(r["sentence_id"]), "text": r["text"]} for r in prior]}
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
    return {"batch": {"batch_id": "target_repair", "sentence_ids": gap_ids}, "request": request,
            "identity": identity, "request_hash": digest(canonical(identity)), "contexts": contexts,
            "schema": schema}


def repair_labels(plan: dict, record: dict) -> tuple[list[str], list[dict]]:
    """Problems with the repair reply, or one label per target."""
    problems, parsed = parse_reply(record["raw_response"], plan["schema"]["schema"], "repairs",
                                   duplicate_keys_ok=False)
    if problems:
        return problems, []
    labels = []
    for i, sid in enumerate(plan["batch"]["sentence_ids"], 1):
        key = f"repair_{i}"
        row = deepcopy(parsed["repairs"][key])
        order = {local_id(r["sentence_id"]): j for j, r in enumerate(plan["contexts"][key])}
        refs = row["context_ids"]
        if len(refs) != len(set(refs)) or any(r not in order for r in refs) \
                or [order[r] for r in refs] != sorted(order[r] for r in refs):
            return [f"{key}: invalid context ids; no repair labels published"], []
        prefix = sid.rsplit("|", 1)[0]
        labels.append({"sentence_id": sid, "label": row["label"], "source": "llm_repair",
                       "context_ids": [f"{prefix}|{c}" for c in refs],
                       "subject_scope": row["subject_scope"], "annotation_note": row["annotation_note"],
                       "is_risk_heading": row["is_risk_heading"], "entity_mentions": row["entity_mentions"],
                       "batch_id": "target_repair", "request_hash": plan["request_hash"],
                       "model": record["raw_response"].get("model"), "prompt_version": REPAIR_VERSION,
                       "runner_version": REPAIR_VERSION})
    return [], labels


def spent_so_far() -> float:
    if not Paths.usage().exists():
        return 0.0
    return sum(r.get("estimated_cost_usd") or 0.0 for r in read_jsonl(Paths.usage()))


# ---------- labels file ----------

def rule_label(unit: dict) -> dict:
    return {"sentence_id": unit["sentence_id"], "label": "not_a_claim", "source": f"rule:{unit['prefilter']}",
            "context_ids": [], "subject_scope": None, "annotation_note": None, "is_risk_heading": False,
            "entity_mentions": []}


def llm_labels(plan: dict, record: dict, results: list[dict], held: list[dict], cfg: dict) -> list[dict]:
    """Labels from an accepted reply, mapped back to full sentence ids."""
    batch = plan["batch"]
    prefix = batch["sentence_ids"][0].rsplit("|", 1)[0]
    held_ids = {h["sentence_id"] for h in held}
    return [{
        "sentence_id": f"{prefix}|{r['id']}", "label": r["label"],
        "source": "code_quarantine" if f"{prefix}|{r['id']}" in held_ids else "llm",
        "context_ids": [f"{prefix}|{c}" for c in r["context_ids"]],
        "subject_scope": r["subject_scope"], "annotation_note": r["annotation_note"],
        "is_risk_heading": r["is_risk_heading"], "entity_mentions": r["entity_mentions"],
        "batch_id": batch["batch_id"], "request_hash": plan["request_hash"],
        "model": record["raw_response"].get("model"), "prompt_version": cfg["prompt_id"],
        "runner_version": RUNNER_VERSION,
    } for r in results]


def usage_report() -> None:
    if not Paths.usage().exists():
        log(STEP, "no API calls recorded yet")
        return
    rows = list(read_jsonl(Paths.usage()))
    tokens = sum(r.get("total_tokens") or 0 for r in rows)
    costs = [r.get("estimated_cost_usd") for r in rows]
    line = f"{len(rows)} requests, {tokens:,} tokens"
    line += f", about ${sum(costs):.4f}" if all(c is not None for c in costs) else " (cost unknown for some)"
    log(STEP, line)


def run(dry_run: bool = False, filing: str | None = None, limit: int | None = None) -> None:
    cfg = settings()["label"]
    units = list(read_jsonl(Paths.sentences()))
    plans = plan_batches(units, cfg)
    write_jsonl(Paths.batches(), [dict(p["batch"], request_hash=p["request_hash"]) for p in plans])

    if dry_run:
        reserve = sum(reserve_cost(p["request"], cfg) for p in plans)
        log(STEP, f"{len(plans)} batches, {sum(len(p['batch']['sentence_ids']) for p in plans)} sentences; "
                  f"worst-case cost ${reserve:.2f} (dry run, no API calls)")

    labeller = Labeller(cfg)
    labels = [rule_label(u) for u in units if u["prefilter"]]
    sent = rejected = complete = 0
    stopped = None
    gaps: dict[str, str] = {}              # sentence id -> "missing" or "ambiguous"
    for plan in plans:
        selected = filing is None or plan["batch"]["accession_number"] == filing
        may_send = not dry_run and not stopped and selected and (limit is None or sent < limit)
        try:
            reply = labeller.label(plan, send=may_send)
        except StopRun as err:
            stopped = str(err)
            log(STEP, f"stopped: {stopped}")
            continue
        sent += reply["sent"]
        if reply["record"] is None:
            continue
        if reply["problems"]:
            rejected += 1
            log(STEP, f"{plan['batch']['batch_id']} rejected: {'; '.join(reply['problems'])[:200]}")
            if cfg["stop_on_failure"] and not stopped:
                stopped = f"{plan['batch']['batch_id']} rejected; see {labeller.folder(plan['request_hash'])}"
                log(STEP, f"stopped: {stopped}")
            continue
        complete += 1
        labels += llm_labels(plan, reply["record"], reply["results"], reply["held"], cfg)
        for h in reply["held"]:
            log(STEP, f"{h['sentence_id']} set aside as needs_review: {', '.join(h['review_flags'])}")
            if h["review_flags"][0] in ("missing_model_target_id", "duplicate_model_target_id"):
                gaps[h["sentence_id"]] = "missing" if h["review_flags"][0] == "missing_model_target_id" else "ambiguous"

    if gaps and complete == len(plans):
        order = {u["sentence_id"]: i for i, u in enumerate(units)}
        repair = plan_repair(sorted(gaps, key=order.get), gaps, units, plans, cfg)
        try:
            record = labeller.cached(repair, kind="targeted_repair")
            if record is None and not dry_run and not stopped and (limit is None or sent < limit):
                record = labeller.send(repair, kind="targeted_repair")
                sent += 1
            if record is None:
                log(STEP, f"{len(gaps)} units need the repair request (not sent in this run)")
            else:
                problems, repaired = repair_labels(repair, record)
                if problems:
                    rejected += 1
                    log(STEP, f"repair rejected: {'; '.join(problems)[:200]}")
                else:
                    done = {l["sentence_id"] for l in repaired}
                    labels = [l for l in labels if l["sentence_id"] not in done] + repaired
                    log(STEP, f"repaired {len(repaired)} units in one request")
        except StopRun as err:
            log(STEP, f"stopped: {err}")
    elif gaps:
        log(STEP, f"{len(gaps)} units wait for the repair request, sent once every batch has a reply")

    order = {u["sentence_id"]: i for i, u in enumerate(units)}
    labels.sort(key=lambda l: order.get(l["sentence_id"], 1 << 30))
    write_jsonl(Paths.labels(), labels)
    log(STEP, f"{sent} new requests, {rejected} rejected replies; {len(labels)}/{len(units)} units labelled "
              f"-> {Paths.labels()}")
    usage_report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="plan batches and estimate cost only")
    parser.add_argument("--filing", metavar="ACCESSION", help="only send requests for this filing")
    parser.add_argument("--limit", type=int, help="send at most this many new requests")
    parser.add_argument("--report", action="store_true", help="print token and cost totals and exit")
    args = parser.parse_args()
    if args.report:
        usage_report()
        return
    run(dry_run=args.dry_run, filing=args.filing, limit=args.limit)


if __name__ == "__main__":
    main()
