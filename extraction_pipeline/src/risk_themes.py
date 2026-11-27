"""Step 4c - Risk themes: ask the LLM which themes each Item 1A risk factor is about (paid, run on purpose).

Input : data/output/sentences.jsonl (step 4), config/risk_taxonomy/themes.csv,
        prompts/risk_themes_v1.txt
Output: data/interim/risk_themes.jsonl              one row per risk factor the model answered
        data/checks/risk_themes/requests.csv        one row per risk factor: its request and whether
                                                    a reply is saved
        data/output/usage_risk_themes.jsonl         this step's spend (its own budget_usd)

A risk factor is one SEC risk: a lead sentence in Item 1A (step 4 lead_source, outside the
risk factor summary) and the units after it, sub-headings below the lead included, up to the
next lead or heading at its level (risk_factors()). Its id, risk_factor_id, is the lead's
unit_id. Units under no lead are not classified here (step 7a counts them).

One request per risk factor: the lead and its units as s1, s2, ... and the 32 themes of
themes.csv with their definitions. The model returns one primary theme (or "unknown") and up
to two secondary themes, each with the sentence ids that support it. It picks themes only,
never groups, and is shown neither the filing's heading nor reference_labels.csv.

Separate from step 5 on purpose: the unit is a whole risk factor, not a section batch, and a
change of the taxonomy changes only these requests, never step 5's request hashes.

Money rules are step 5's (label.Labeller): a request whose reply is saved
(data/risk_themes/<hash>/) is never sent again, nothing is resent automatically, and the run
stops before budget_usd. The request holds only text (no accession, no unit_id), so a risk
factor repeated word for word in another filing has the same request hash and reuses the reply:
the cost is paid once per distinct risk text.

Usage:
  python -m src.risk_themes --dry-run              requests, how many are saved, estimated cost
  python -m src.risk_themes --dry-run --compare    cost with every model in settings prices:
  python -m src.risk_themes --limit 3              send at most 3 new requests
  python -m src.risk_themes --workers 8            8 requests in flight at once
then step 7a (risk_factors) puts the themes on every unit, and step 7b on every Claim.
"""

from __future__ import annotations

import argparse
from collections import Counter

from . import llm_cost, risk_taxonomy
from .common import PROMPTS_DIR, Paths, checks_dir, log, read_jsonl, write_csv, write_jsonl

STEP = "risk_themes"
RUNNER_VERSION = "kelly_risk_themes_v1"
PROMPT_VERSION = "risk_themes_v1"
SHOWN_PREFILTERS = (None, "list_intro", "heading")   # shown to the model; cross references, tables etc. are not
UNIT_ROLES = ("body", "list_group")          # besides headings, the roles a risk factor covers
MAX_SECONDARY = 2

REQUEST_FIELDS = ["risk_factor_id", "ticker", "form", "accession_number", "section_key", "n_units", "n_shown",
                  "truncated", "lead_text", "request_hash", "reply_saved"]


# ---------- risk factors ----------

def risk_factors(units: list[dict]) -> list[dict]:
    """One entry per Item 1A lead outside the risk factor summary, with the units it covers, in order.

    A risk factor runs from its lead to the next lead, the next heading at its own level or above,
    or the end of the section. Its own level is that of the heading above the lead (at least 1,
    so a category heading always ends it); a lead that follows another open risk factor is its
    sibling and takes the same level. So a sub-heading below the lead, such as Microsoft's italic
    sub-topics ("Competition in the technology sector") under a bold lead, stays inside the risk
    factor, and so do the sentences under it.

    This is wider than step 4's lead_unit_id, which stops at any heading (kept as decided on
    26 Nov: the sentences under a sub-topic need no link to the lead). risk_factor_id is only
    the risk factor's id, used for its themes.

    units: the entry's units, the lead first (unit_ids of every unit that takes its themes).
    """
    out, current, section, heading_level = [], None, None, 0
    for u in units:
        if u["section_type"] != "risk_factors":
            continue
        if (u["accession_number"], u["section_key"]) != section:
            section, current, heading_level = (u["accession_number"], u["section_key"]), None, 0
        if u.get("risk_summary") or u["role"] == "section_title":
            current = None
            continue
        if u["role"] == "heading":
            level = u.get("heading_level")
            if isinstance(level, int) and level > 0:
                if current is not None and level <= current["level"]:
                    current = None
                heading_level = level
            if current is not None:
                current["units"].append(u)
            continue
        if u["role"] not in UNIT_ROLES:
            continue
        if u.get("lead_source"):
            level = current["level"] if current is not None else max(heading_level, 1)
            current = {"risk_factor_id": u["unit_id"], "accession_number": u["accession_number"],
                       "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                       "lead_text": u["text"], "level": level, "units": [u]}
            out.append(current)
        elif current is not None:
            current["units"].append(u)
    return out


def risk_factor_of(units: list[dict]) -> dict[str, str]:
    """unit_id -> risk_factor_id, for the lead itself and every unit under it."""
    return {u["unit_id"]: rf["risk_factor_id"] for rf in risk_factors(units) for u in rf["units"]}


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
    """The units the model reads: the lead, then the units step 5 would label and the sub-headings."""
    lead, rest = rf["units"][0], [u for u in rf["units"][1:] if u.get("prefilter") in SHOWN_PREFILTERS]
    return ([lead] + rest)[:max_units]


def plans(factors: list[dict], cfg: dict) -> list[dict]:
    """One request per risk factor. The request has text only, so identical risk text shares a hash."""
    from .label import canonical, digest
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
    from .label import parse_reply
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


# ---------- run ----------

def run(dry_run: bool = False, limit: int | None = None, model: str | None = None,
        compare: bool = False, workers: int | None = None) -> None:
    from .label import Labeller, StopRun
    cfg = llm_cost.step_cfg(STEP, model)
    factors = risk_factors(list(read_jsonl(Paths.sentences())))
    todo = plans(factors, cfg)
    distinct = unique(todo)
    labeller = Labeller(cfg, Paths.step_usage(STEP))
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
