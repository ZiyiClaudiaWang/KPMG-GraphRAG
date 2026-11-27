"""Step 7 requests: the source check, the prompt, the schema and the batches."""

from __future__ import annotations

from collections import defaultdict

import json

from .. import source_review
from ..common import PROMPTS_DIR, Paths, filings, load_config, read_jsonl, source_text, unit_id
from ..entities import annotation as entity_annotation
from ..llm.client import canonical, digest
from ..validate import LABEL_DEFAULTS, LABELS, SUBJECT_SCOPES  # noqa: F401  (LABEL_DEFAULTS re-exported)

STEP = "label"
RUNNER_VERSION = "kelly_checked_openai_v3_postchecks"   # Claudia's runner id; part of every request hash


def label_fields(row: dict, text: str | None = None) -> dict:
    """The model's fields of one result, checked: at most 3 known subject scopes (company when
    none), and a time_reference that is not part of the sentence becomes null."""
    scopes = [s for s in dict.fromkeys(row.get("subject_scope") or []) if s in SUBJECT_SCOPES][:3]
    when = row.get("time_reference")
    if when is not None and (not when.strip() or (text is not None and when not in text)):
        when = None
    return {"subject_scope": scopes or ["company"], "annotation_note": row.get("annotation_note"),
            "is_boilerplate": row.get("is_boilerplate"), "time_reference": when}


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
    if len({u["unit_id"] for u in units}) != len(units):
        raise ValueError("Duplicate unit ids in sentences.jsonl: two units share the same source spans.")
    for u in units:
        raw = sources[(u["accession_number"], u["section_key"])]
        for key in ("section_id", "processing_set_id", "source_locator", "form", "ticker"):
            if u[key] != raw[key]:
                raise ValueError(f"{u['unit_id']}: {key} differs from the section export.")
        cursor = -1
        if not u["raw_spans"]:
            raise ValueError(f"{u['unit_id']}: no raw_spans.")
        for start, end in u["raw_spans"]:
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw["content"]) \
                    or start < cursor:
                raise ValueError(f"{u['unit_id']}: invalid or overlapping raw_spans.")
            cursor = end
        if source_text(raw["content"], u["raw_spans"]) != u["text"]:
            raise ValueError(f"{u['unit_id']}: text cannot be rebuilt from the source.")
        if unit_id(u["accession_number"], u["section_key"], u["raw_spans"]) != u["unit_id"]:
            raise ValueError(f"{u['unit_id']}: unit_id does not match its raw_spans; rerun step 4.")


def entity_catalog() -> list[dict]:
    path = Paths.entity_catalog()
    catalog = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entity_annotation.validate_catalog(catalog)
    return catalog


# ---------- prompt, schema, request ----------

def batch_numbers(context: list[dict], selected: list[dict]) -> tuple[list[str], list[str]]:
    """The short numbers the model sees in one request: c1, c2, ... for the context units and
    s1, s2, ... for the sentences to label, in order. Used inside requests and replies only."""
    return [f"c{i}" for i in range(1, len(context) + 1)], [f"s{i}" for i in range(1, len(selected) + 1)]


def context_units(section_units: list[dict], pos: int, targets: list[dict], cfg: dict) -> list[dict]:
    """The context for units starting at section position pos: the context_sentences units
    before it (section titles left out), plus any missing list intro (list_intros()), all in
    source order."""
    near = [u for u in section_units[:pos] if u["role"] != "section_title"][-cfg["context_sentences"]:]
    chosen = {u["unit_id"] for u in near + list_intros(section_units, targets, near)}
    return [u for u in section_units if u["unit_id"] in chosen]


def list_intros(section_units: list[dict], targets: list[dict], near: list[dict]) -> list[dict]:
    """Intro sentences of split list items among targets that are neither among the targets nor
    in the nearby context. Usually the intro is further up than that context; an intro too
    short to be labelled ("Solutions include:", prefilter list_intro) can also sit among the
    batch's own sentences, and is then only visible this way."""
    present = {u["unit_id"] for u in targets} | {u["unit_id"] for u in near}
    wanted = {u["list_intro_id"] for u in targets if u.get("list_intro_id")} - present
    return [u for u in section_units if u["unit_id"] in wanted]


def system_prompt(prompt_version: str) -> str:
    """prompts/<version>.txt with the label definitions and rules from config/claim_types.yaml."""
    types = load_config("claim_types")
    definitions = [f"- {name}: {text}" for name, text in types["claim_types"].items()]
    definitions.append(f"- not_a_claim: {types['not_a_claim']}")
    rules = "\n".join(f"- {r}" for r in types["rules"])
    template = (PROMPTS_DIR / f"{prompt_version}.txt").read_text(encoding="utf-8")
    return template.format(label_definitions="\n".join(definitions), rules=rules,
                           subject_scopes=", ".join(SUBJECT_SCOPES))


def user_payload(batch: dict, units: dict[str, dict]) -> dict:
    return {
        "filing": batch["filing"],
        "section": units[batch["unit_ids"][0]]["section_path"].split(" > ")[0],
        "context": [{"id": n, "text": units[u]["text"]}
                    for u, n in zip(batch["context_unit_ids"], batch["context_local_ids"])],
        "sentences": [{"id": n, "section_path": units[u]["section_path"], "text": units[u]["text"]}
                      for u, n in zip(batch["unit_ids"], batch["local_ids"])],
    }


def response_schema() -> dict:
    """label_v3: no is_risk_heading, no entity_mentions (step 8b extracts those from Claims)."""
    result = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "label", "context_ids", "subject_scope", "annotation_note", "is_boilerplate",
                     "time_reference"],
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string", "enum": list(LABELS)},
            "context_ids": {"type": "array", "items": {"type": "string"}},
            "subject_scope": {"type": "array", "items": {"type": "string", "enum": list(SUBJECT_SCOPES)}},
            "annotation_note": {"type": ["string", "null"]},
            "is_boilerplate": {"type": "boolean"},
            "time_reference": {"type": ["string", "null"]},
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


def build_request(batch: dict, units: dict[str, dict], cfg: dict, system: str, schema: dict | None = None) -> dict:
    request = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": canonical(user_payload(batch, units))}],
        "response_format": {"type": "json_schema", "json_schema": schema or response_schema()},
        "max_completion_tokens": cfg["max_completion_tokens"],
        "store": False,                          # OpenAI does not keep the request
    }
    # Only these model families take temperature; newer reasoning models may reject it.
    if cfg["model"].startswith(("gpt-4.1", "gpt-4o")):
        request["temperature"] = cfg["temperature"]
    return request


# ---------- batches ----------

def request_key(request: dict, runner: str = RUNNER_VERSION, retry_of: str | None = None) -> dict:
    """What a request_hash covers: the request exactly as sent, and the code that reads the reply.

    Nothing else goes in: a field that never reaches the model cannot change its reply, so it
    must not make the run pay for the same request again.
    """
    key = {"runner_version": runner, "request": request}
    if retry_of:
        key["retry_of"] = retry_of
    return key


def plan_batches(units: list[dict], cfg: dict, skip: set[str] | frozenset = frozenset(),
                 only: set[str] | None = None, system: str | None = None, schema: dict | None = None,
                 runner: str = RUNNER_VERSION) -> list[dict]:
    """One plan per request: consecutive labelable units of a section, batch_max_sentences at a time.

    skip: unit ids not to send (reused labels); only: send just these. A batch never spans a
    labelable unit that is not sent, so the units before each batch are its own context.

    A plan holds the batch, the request, and the identity (request plus the source versions
    it depends on, saved in request.json). request_hash = hash of request_key(): only what
    reaches the model; it names the cache folder.
    A batch whose request exceeds max_request_bytes is halved until it fits.
    system, schema, runner: another step's prompt, reply schema and runner id (step 8b).
    """
    sources, _ = source_index()
    verify_units(units, sources)
    system = system or system_prompt(cfg["prompt_version"])
    lookup = {u["unit_id"]: u for u in units}
    vocabulary = source_review.source_vocabulary(sources)
    catalog = entity_catalog()

    by_section: dict[tuple, list[dict]] = defaultdict(list)
    for u in units:
        by_section[(u["accession_number"], u["section_key"])].append(u)

    plans = []
    for (accession, section_key), section_units in sorted(by_section.items()):
        source = sources[(accession, section_key)]
        sentences = [u for u in section_units if u["prefilter"] is None]
        runs, run, offset = [], [], 0                 # (index of the run's first sentence, run)
        for k, u in enumerate(sentences):
            if u["unit_id"] in skip or (only is not None and u["unit_id"] not in only):
                if run:
                    runs.append((offset, run))
                run = []
                continue
            if not run:
                offset = k
            run.append(u)
        if run:
            runs.append((offset, run))
        for offset, eligible in runs:
            start = 0
            while start < len(eligible):
                size = min(cfg["batch_max_sentences"], len(eligible) - start)
                while True:
                    selected = eligible[start:start + size]
                    first = next(i for i, u in enumerate(section_units) if u["unit_id"] == selected[0]["unit_id"])
                    context = context_units(section_units, first, selected, cfg)
                    context_numbers, numbers = batch_numbers(context, selected)
                    batch = {
                        "batch_id": f"{accession}|{section_key}|checked_start_{offset + start}_n{len(selected)}",
                        "accession_number": accession, "section_key": section_key,
                        "section_type": selected[0]["section_type"],
                        "filing": f"{source['ticker']} {source['form']}",
                        "unit_ids": [u["unit_id"] for u in selected],
                        "context_unit_ids": [u["unit_id"] for u in context],
                        "local_ids": numbers,
                        "context_local_ids": context_numbers,
                    }
                    request = build_request(batch, lookup, cfg, system, schema)
                    if len(canonical(request).encode("utf-8")) <= cfg["max_request_bytes"] or size == 1:
                        break
                    size = max(1, size // 2)
                identity = {
                    "runner_version": runner, "request": request, "batch": batch,
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
                              "request_hash": digest(canonical(request_key(request, runner)))})
                start += size
    return plans

