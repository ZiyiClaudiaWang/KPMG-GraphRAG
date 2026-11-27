"""Step 5b - Risk themes: the LLM says which themes each Item 1A risk factor is about (paid).

Input : data/interim/risk_factors.jsonl and risk_assignments.jsonl (step 5),
        data/output/sentences.jsonl (step 4, the units' text), config/risk_taxonomy/themes.csv,
        prompts/risk_themes_v1.txt
Output: data/interim/risk_themes.jsonl                  one row per risk factor the model answered
        data/output/usage_risk_themes.jsonl             this step's spend (its own budget_usd)
        data/checks/risk_themes/requests.csv            one row per risk factor: its request, reply saved?
        data/checks/risk_themes/risk_factor_themes.csv  one row per risk factor, for review

One request per risk factor (step 5 defines them): the lead and its units as s1, s2, ... and the
32 themes of themes.csv with their definitions. The model returns one primary theme (or
"unknown") and up to two secondary themes, each with the sentence ids that support it. It picks
themes only, never groups, and is shown neither the filing's heading nor reference_labels.csv.

Separate from step 7 (label) on purpose: the unit is a whole risk factor, not a section batch,
and a change of the taxonomy changes only these requests, never step 7's request hashes.

Sends only when asked: a default run (`run_pipeline.py` without --steps risk_themes) reads the
saved replies and writes the outputs, but sends nothing. Money rules are the shared client's
(src/llm/client.py): a request whose reply is saved (data/risk_themes/<hash>/) is never sent
again, nothing is resent automatically, and the run stops before budget_usd. The request holds
only text (no accession, no unit_id), so a risk factor repeated word for word in another filing
has the same request hash and reuses the reply: the cost is paid once per distinct risk text.

Themes reach the Claims in step 9 (build_claims), by risk_factor_id: apply() puts the primary
theme on every unit of the risk factor (scope risk_factor) and a secondary only on the units
the model gave for it (scope sentence). An answer whose unit list differs from the risk factor's
now (step 4 or 5 changed it since) is not used: those units are not_classified.

Usage:
  python run_pipeline.py --steps risk_themes --dry-run     requests, saved replies, estimated cost
  python -m src.risk.themes --dry-run --compare            cost with every model in settings prices:
  python run_pipeline.py --steps risk_themes --limit 3     send at most 3 new requests
  python run_pipeline.py --steps risk_themes --workers 8   8 requests in flight at once
"""

from __future__ import annotations

import argparse
from collections import Counter

from . import factors as risk_factors, taxonomy as risk_taxonomy
from ..llm import cost as llm_cost
from ..common import PROMPTS_DIR, Paths, checks_dir, log, read_jsonl, write_csv, write_jsonl
from .taxonomy import category_key

STEP = "risk_themes"
RUNNER_VERSION = "kelly_risk_themes_v1"
PROMPT_VERSION = "risk_themes_v1"
SHOWN_PREFILTERS = (None, "list_intro", "heading")   # shown to the model; cross references, tables etc. are not
MAX_SECONDARY = 2

THEME_SOURCE = "llm"           # HAS_RISK_THEME.source: themes come from this step
THEME_STATUSES = ["classified", "unknown", "not_classified", "no_risk_factor", "risk_summary"]
FACTOR_FIELDS = ["risk_factor_id", "ticker", "form", "accession_number", "category_text", "heading_risk_group",
                 "lead_text", "n_units", "primary_theme", "primary_group", "secondary_themes",
                 "agrees_with_heading", "reference_theme", "review"]
REQUEST_FIELDS = ["risk_factor_id", "ticker", "form", "accession_number", "section_key", "n_units", "n_shown",
                  "truncated", "lead_text", "request_hash", "reply_saved"]


# ---------- requests ----------

def system_prompt() -> str:
    text = (PROMPTS_DIR / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    return text + "\n".join(f"{t}: {row['definition']}" for t, row in risk_taxonomy.themes().items()) + "\n"


def schema(theme_ids: list[str]) -> dict:
    secondary = {"type": "object", "additionalProperties": False, "required": ["theme_id", "sentence_ids"],
                 "properties": {"theme_id": {"type": "string", "enum": theme_ids},
                                "sentence_ids": {"type": "array", "items": {"type": "string"}}}}
    result = {"type": "object", "additionalProperties": False, "required": ["primary_theme", "secondary_themes"],
              "properties": {"primary_theme": {"type": "string", "enum": theme_ids + [risk_taxonomy.UNKNOWN]},
                             "secondary_themes": {"type": "array", "items": secondary}}}
    return {"name": "risk_themes", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": ["result"],
                       "properties": {"result": result}}}


def shown(rf: dict, max_units: int) -> list[dict]:
    """The units the model reads: the lead, then the units step 7 would label and the sub-headings."""
    lead, rest = rf["units"][0], [u for u in rf["units"][1:] if u.get("prefilter") in SHOWN_PREFILTERS]
    return ([lead] + rest)[:max_units]


def plans(factors: list[dict], cfg: dict) -> list[dict]:
    """One request per risk factor. The request has text only, so identical risk text shares a hash."""
    from ..llm.client import canonical, digest
    system, ids = system_prompt(), list(risk_taxonomy.themes())
    version = risk_taxonomy.version()
    out = []
    for rf in factors:
        units = shown(rf, int(cfg.get("max_units", 80)))
        sentences = [{"id": f"s{k + 1}", "text": u["text"]} for k, u in enumerate(units)]
        request = {"model": cfg["model"],
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": canonical({"risk_factor": sentences})}],
                   "response_format": {"type": "json_schema", "json_schema": schema(ids)},
                   "store": False}
        llm_cost.model_params(request, cfg, answer_tokens=int(cfg.get("answer_tokens", 300)))
        key = {"runner_version": RUNNER_VERSION, "request": request}
        batch = {"batch_id": f"{rf['risk_factor_id']}|risk_themes", "risk_factor_id": rf["risk_factor_id"],
                 "unit_ids": [u["unit_id"] for u in units]}
        out.append({"batch": batch, "request": request, "request_hash": digest(canonical(key)),
                    "identity": {"runner_version": RUNNER_VERSION, "prompt_version": PROMPT_VERSION,
                                 "taxonomy_version": version, "request": request, "batch": batch},
                    "factor": rf, "truncated": len(units) < 1 + sum(u.get("prefilter") in SHOWN_PREFILTERS
                                                                    for u in rf["units"][1:])})
    return out


def unique(todo: list[dict]) -> list[dict]:
    """One plan per request hash (identical risk text is sent once)."""
    seen, out = set(), []
    for p in todo:
        if p["request_hash"] not in seen:
            seen.add(p["request_hash"])
            out.append(p)
    return out


def expected_answer_tokens(todo: list[dict]) -> list[int]:
    """About 15 tokens for the primary theme and 25 per secondary with a few ids, plus the envelope."""
    return [70 for _ in todo]


# ---------- replies ----------

def answer(plan: dict, record: dict) -> tuple[list[str], list[str], dict | None]:
    """(problems that reject the reply, notes on parts dropped, the result in unit_ids).

    Dropped, with a note: a secondary equal to the primary or to an earlier secondary, more than
    two, sentence ids that were not sent, a secondary left with no sentence.
    """
    from ..llm.client import parse_reply
    problems, parsed = parse_reply(record["raw_response"], schema(list(risk_taxonomy.themes()))["schema"],
                                   "result", duplicate_keys_ok=False)
    if problems:
        return problems, [], None
    r = parsed["result"]
    local = {f"s{k + 1}": u for k, u in enumerate(plan["batch"]["unit_ids"])}
    notes, secondary, used = [], [], {r["primary_theme"]}
    for s in r["secondary_themes"]:
        if s["theme_id"] in used:
            notes.append(f"secondary {s['theme_id']} repeats a theme: dropped")
            continue
        if len(secondary) == MAX_SECONDARY:
            notes.append(f"secondary {s['theme_id']} beyond {MAX_SECONDARY}: dropped")
            continue
        bad = [i for i in s["sentence_ids"] if i not in local]
        if bad:
            notes.append(f"secondary {s['theme_id']}: ids not sent {bad} dropped")
        unit_ids = list(dict.fromkeys(local[i] for i in s["sentence_ids"] if i in local))
        if not unit_ids:
            notes.append(f"secondary {s['theme_id']} has no sentence: dropped")
            continue
        used.add(s["theme_id"])
        secondary.append({"theme_id": s["theme_id"], "unit_ids": unit_ids})
    return [], notes, {"primary_theme": r["primary_theme"], "secondary_themes": secondary}


def load_results() -> dict[str, dict]:
    """risk_factor_id -> this step's row; empty when the step has not run."""
    path = Paths.risk_themes()
    return {r["risk_factor_id"]: r for r in read_jsonl(path)} if path.exists() else {}



# ---------- themes on the units (used by step 9 and the report) ----------

def current(factors: list[dict], results: dict[str, dict]) -> dict[str, dict]:
    """risk_factor_id -> this step's answer, only where it still fits: the same units as the risk
    factor now (step 4 or 5 did not change it since) and themes of the current taxonomy."""
    themes = risk_taxonomy.themes()
    out = {}
    for rf in factors:
        r = results.get(rf["risk_factor_id"])
        fits = r and r["unit_ids"] == rf["unit_ids"]
        known = r and (r["primary_theme"] in themes or r["primary_theme"] == risk_taxonomy.UNKNOWN) \
            and all(x["theme_id"] in themes for x in r["secondary_themes"])     # not from an older taxonomy
        if fits and known:
            out[rf["risk_factor_id"]] = r
    return out


def apply(assignments: list[dict], factors: list[dict], results: dict[str, dict]) -> list[dict]:
    """Put risk_theme_status and risk_themes on each step-5 assignment (in place; also returned).

    assignments: step 5 rows (risk_factor_id, heading_risk_group, risk_summary); factors: step 5
    risk factors (unit_ids); results: this step's rows by risk_factor_id (load_results()).
    """
    themes, answered = risk_taxonomy.themes(), current(factors, results)
    for a in assignments:
        a["risk_themes"] = []
        if a.get("risk_factor_id") is None:
            a["risk_theme_status"] = "risk_summary" if a.get("risk_summary") else "no_risk_factor"
            continue
        r = answered.get(a["risk_factor_id"])
        if r is None:
            a["risk_theme_status"] = "not_classified"
            continue
        edge = {"source": THEME_SOURCE, "model": r["model"], "taxonomy_version": r["taxonomy_version"]}
        primary = r["primary_theme"]
        a["risk_theme_status"] = "unknown" if primary == risk_taxonomy.UNKNOWN else "classified"
        if primary != risk_taxonomy.UNKNOWN:
            heading = a["heading_risk_group"]
            agrees = (themes[primary]["group_id"] in heading) if heading else None
            a["risk_themes"].append({"theme_id": primary, "rank": "primary", "scope": "risk_factor",
                                     "agrees_with_heading": agrees, **edge})
        for s in r["secondary_themes"]:
            if a["unit_id"] in s["unit_ids"]:
                a["risk_themes"].append({"theme_id": s["theme_id"], "rank": "secondary", "scope": "sentence",
                                         "agrees_with_heading": None, **edge})
    return assignments


def review_rows(factors: list[dict], assignments: list[dict], results: dict[str, dict]) -> list[dict]:
    """One row per risk factor for risk_factor_themes.csv; review: not_classified, unknown,
    differs_from_reference (reference_labels.csv), overrules_heading."""
    themes, answered = risk_taxonomy.themes(), current(factors, results)
    by_unit = {a["unit_id"]: a for a in assignments}
    reference = risk_taxonomy.reference_labels()
    rows = []
    for rf in factors:
        r, lead = answered.get(rf["risk_factor_id"]), by_unit.get(rf["risk_factor_id"], {})
        primary = r["primary_theme"] if r else None
        heading = lead.get("heading_risk_group") or []
        agrees = (themes[primary]["group_id"] in heading) if heading and primary in themes else None
        ref = reference.get(category_key(rf["lead_text"]), "")
        review = ([] if r else ["not_classified"]) + (["unknown"] if primary == risk_taxonomy.UNKNOWN else []) \
            + (["differs_from_reference"] if r and ref and primary != ref else []) \
            + (["overrules_heading"] if agrees is False else [])
        rows.append({"risk_factor_id": rf["risk_factor_id"], "ticker": rf["ticker"], "form": rf["form"],
                     "accession_number": rf["accession_number"], "category_text": lead.get("risk_category_text"),
                     "heading_risk_group": ";".join(heading), "lead_text": rf["lead_text"],
                     "n_units": len(rf["unit_ids"]), "primary_theme": primary,
                     "primary_group": themes[primary]["group_id"] if primary in themes else None,
                     "secondary_themes": ";".join(f"{s['theme_id']}({len(s['unit_ids'])})"
                                                  for s in (r["secondary_themes"] if r else [])),
                     "agrees_with_heading": agrees, "reference_theme": ref, "review": ";".join(review)})
    return rows


def factors_path():
    return checks_dir(STEP) / "risk_factor_themes.csv"


# ---------- run ----------

def with_units(factors: list[dict], units: list[dict]) -> list[dict]:
    """Step 5's risk factors with their unit records (units), as plans() needs them."""
    by_id = {u["unit_id"]: u for u in units}
    return [dict(rf, units=[by_id[i] for i in rf["unit_ids"]]) for rf in factors]


def run(dry_run: bool = False, limit: int | None = None, model: str | None = None,
        compare: bool = False, workers: int | None = None, send: bool = True) -> None:
    """send=False (a default run): only the saved replies are read, nothing is sent."""
    from ..llm.client import Client, StopRun
    cfg = llm_cost.step_cfg(STEP, model)
    factors = with_units(risk_factors.load_factors(), list(read_jsonl(Paths.sentences())))
    todo = plans(factors, cfg)
    distinct = unique(todo)
    labeller = Client(cfg, Paths.step_usage(STEP))
    saved = {p["request_hash"] for p in distinct if labeller.cached(p, STEP) is not None}
    log(STEP, f"{len(factors)} risk factors, {len(distinct)} distinct requests ({len(saved)} with a saved "
              f"reply), model {cfg['model']}"
              + (f", reasoning_effort {cfg.get('reasoning_effort')}" if llm_cost.is_reasoning(cfg["model"]) else ""))
    truncated = sum(p["truncated"] for p in todo)
    if truncated:
        log(STEP, f"{truncated} risk factors longer than max_units ({cfg.get('max_units', 80)}): only their "
                  "first units are shown and can carry a secondary theme")
    new = [p for p in distinct if p["request_hash"] not in saved]
    llm_cost.report(STEP, cfg, lambda c: [p for p in unique(plans(factors, c))
                                          if p["request_hash"] not in saved or c["model"] != cfg["model"]],
                    expected_answer_tokens, log, compare)
    write_requests(todo, saved)
    if dry_run:
        return
    if not send:
        limit = 0
        if new:
            log(STEP, f"{len(new)} requests without a saved reply not sent (a default run sends nothing): "
                      "run `python run_pipeline.py --steps risk_themes` to send them")

    workers = max(1, int(workers or cfg.get("concurrency") or 1))
    send = new if limit is None else new[:limit]
    state = {"sent": 0, "stopped": None}

    def one(plan: dict) -> None:
        labeller.send(plan, STEP)
        state["sent"] += 1

    try:
        if workers == 1:
            for plan in send:
                one(plan)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(one, p) for p in send]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except StopRun as err:
                        if not state["stopped"]:
                            state["stopped"] = str(err)
                            for g in futures:
                                g.cancel()
    except StopRun as err:
        state["stopped"] = str(err)
    if state["stopped"]:
        log(STEP, f"stopped: {state['stopped']}")

    rows, failed, notes_n = [], 0, 0
    for plan in todo:
        record = labeller.cached(plan, STEP)
        if record is None:
            continue
        problems, notes, result = answer(plan, record)
        if problems:
            failed += 1
            log(STEP, f"{plan['batch']['batch_id']} rejected: {'; '.join(problems)[:200]}")
            continue
        notes_n += bool(notes)
        rf = plan["factor"]
        rows.append({"risk_factor_id": rf["risk_factor_id"], "accession_number": rf["accession_number"],
                     "ticker": rf["ticker"], "form": rf["form"], "section_key": rf["section_key"],
                     "unit_ids": [u["unit_id"] for u in rf["units"]], "shown_unit_ids": plan["batch"]["unit_ids"],
                     **result, "notes": notes, "request_hash": plan["request_hash"],
                     "runner_version": RUNNER_VERSION, "prompt_version": PROMPT_VERSION,
                     "taxonomy_version": plan["identity"]["taxonomy_version"],
                     "model": record["raw_response"].get("model") or cfg["model"]})
    write_jsonl(Paths.risk_themes(), rows)
    results = {r["risk_factor_id"]: r for r in rows}
    review = review_rows(factors, list(read_jsonl(Paths.risk_assignments())), results)
    write_csv(factors_path(), review, FACTOR_FIELDS)
    log(STEP, f"risk factors to review {dict(Counter(x for r in review for x in r['review'].split(';') if x))} "
              f"-> {factors_path()}")
    write_requests(todo, {p["request_hash"] for p in distinct if labeller.cached(p, STEP) is not None})
    primary = Counter(r["primary_theme"] for r in rows)
    log(STEP, f"{state['sent']} requests sent (about ${labeller.spent:.4f} spent by this step in all); "
              f"{len(rows)} of {len(factors)} risk factors answered, {failed} rejected, {notes_n} with parts "
              f"dropped; unknown {primary[risk_taxonomy.UNKNOWN]} -> {Paths.risk_themes()}")


def write_requests(todo: list[dict], saved: set[str]) -> None:
    path = checks_dir(STEP) / "requests.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(path, [{**{k: p["factor"][k] for k in ("risk_factor_id", "ticker", "form", "accession_number",
                                                       "section_key", "lead_text")},
                      "n_units": len(p["factor"]["units"]), "n_shown": len(p["batch"]["unit_ids"]),
                      "truncated": p["truncated"], "request_hash": p["request_hash"],
                      "reply_saved": p["request_hash"] in saved} for p in todo], REQUEST_FIELDS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="send at most this many new requests")
    ap.add_argument("--model", help="use this model instead of settings.yaml risk_themes.model")
    ap.add_argument("--compare", action="store_true", help="cost with every model in settings prices:")
    ap.add_argument("--workers", type=int, help="requests in flight at once")
    args = ap.parse_args()
    run(dry_run=args.dry_run or args.compare, limit=args.limit, model=args.model, compare=args.compare,
        workers=args.workers)
