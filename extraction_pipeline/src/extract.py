"""Step 8b - Extract: what each Claim names, counts and is about (OpenAI; paid).

Input : data/output/sentences.jsonl, data/output/labels.jsonl (step 7),
        data/output/review_queue.jsonl (step 8: held units are left out)
Output: data/output/extractions.jsonl     one row per Claim unit (read by step 9)
        data/output/usage_extract.jsonl   this step's ledger (budget extract.budget_usd)
        data/extract/<hash>/              saved requests and replies (never sent twice)

Runs after step 8, on the units that become Claims: labelled stated_fact, forward_looking or
hedged_risk and not held. not_a_claim and needs_review units are never sent, so a change here
never touches step 7's labels or their saved replies, and the other way round.

Batches are step 7's (label/requests.py plan_batches: up to batch_max_sentences consecutive
Claims of a section, the 4 units before them as context), with prompts/extract_v1.txt and its
own schema. Each result, per sentence:
  entity_mentions  [{mention_text, entity_type, role, reference_kind, evidence_quote}]
                   entity_type: segment, subsidiary (matched to the catalog in step 9, as
                   step 7's mentions were), organization, product, geography, regulation,
                   government, person, unknown (kept on the Claim, no node yet)
  metrics          [{metric_text, value_text, value, unit, period_text, direction}]
  event_type       one of EVENT_TYPES or null
  affected_object  0 to 3 of AFFECTED_OBJECTS
Code checks every quoted field against the sentence: a mention whose mention_text or
evidence_quote is not part of it is dropped, a metric whose metric_text or value_text is not part
of it is dropped, a period_text that is not part of it becomes null. There is no separate
validate step after 8b: an extraction only adds to a Claim and never decides whether it is one.
A reply whose ids are all from the batch but leaves some out or repeats them keeps the rows of
the ids given once; a reply cut off at max_completion_tokens keeps none. Either way those Claims are
sent once more after the first round, in batches of at most RETRY_BATCH (runner id .../retry, so
they are new requests); what still fails then is in rejected_batches.jsonl and missing.jsonl.
What code dropped, rejected batches and Claims still without an extraction are review files:
data/checks/extract/dropped.jsonl, rejected_batches.jsonl, missing.jsonl. Step 9 then checks
the segment / subsidiary mentions against the catalog (entity_review_flags).

Repeats: a Claim whose label step 7 reused (label.reuse_repeats, same text, context, path, form
and list intro as in an earlier filing) gets a copy of that first occurrence's extraction
(source llm_reused). A reused Claim whose first occurrence has no extraction yet is sent.

  python -m src.extract --dry-run      plan, count, estimate cost (typical and worst case)
  python -m src.extract --limit 3      send at most 3 new requests
  python -m src.extract --workers 8    8 requests in flight at once
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .common import PROMPTS_DIR, Paths, Progress, checks_dir, log, read_jsonl, settings, write_jsonl
from .label.requests import plan_batches
from .label.reuse import reuse_plan
from .llm.client import Client, StopRun, canonical, parse_reply
from .llm.cost import REASONING_GUESS, is_reasoning, step_cfg
from .validate import CLAIM_TYPES, held_ids, reviewed_labels

STEP = "extract"
RUNNER_VERSION = "kelly_extract_v1"
RETRY_BATCH = 5            # Claims a batch in the retry round (left-out or cut-off replies)
KIND = "extract"                        # folder of saved requests under data/

ENTITY_TYPES = ("segment", "subsidiary", "organization", "product", "geography", "regulation", "government",
                "person", "unknown")
CATALOG_TYPES = ("segment", "subsidiary", "unknown")     # matched to the entity catalog in step 9
ROLES = ("subject", "customer", "supplier", "competitor", "partner", "acquired_or_divested", "affected",
         "location", "authority", "other")
EVENT_TYPES = ("acquisition", "divestiture", "merger", "restructuring", "litigation", "regulatory_action",
               "product_launch", "investment", "financing", "share_repurchase", "dividend", "impairment",
               "cyber_incident", "supply_disruption", "labor", "leadership_change", "other")
AFFECTED_OBJECTS = ("revenue", "margin", "costs", "cash_flow", "liquidity", "demand", "supply_chain",
                    "operations", "reputation", "legal_or_regulatory", "workforce", "technology",
                    "financial_condition", "stock_price", "other")
UNITS = ("USD", "percent", "percentage_points", "shares", "employees", "units", "other")
DIRECTIONS = ("increase", "decrease", "unchanged", "none")
EMPTY = {"entity_mentions": [], "metrics": [], "event_type": None, "affected_object": []}


def config(model: str | None = None) -> dict:
    """extract: settings with prices (llm/cost.py), plus step 7's batching keys."""
    cfg = {k: v for k, v in settings()["label"].items()
           if k in ("context_sentences", "batch_max_sentences", "max_request_bytes", "temperature",
                    "reuse_repeats")}
    cfg.update(step_cfg(STEP, model))
    return cfg


def system_prompt(version: str) -> str:
    template = (PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")
    return template.format(entity_types=", ".join(ENTITY_TYPES), roles=", ".join(ROLES),
                           event_types=", ".join(EVENT_TYPES), affected_objects=", ".join(AFFECTED_OBJECTS))


def response_schema() -> dict:
    s = lambda: {"type": "string"}                                   # noqa: E731
    opt = lambda: {"type": ["string", "null"]}                       # noqa: E731
    enum = lambda values, null=False: {"type": ["string", "null"] if null else "string",  # noqa: E731
                                       "enum": list(values) + ([None] if null else [])}
    obj = lambda props: {"type": "object", "additionalProperties": False,  # noqa: E731
                         "required": list(props), "properties": props}
    mention = obj({"mention_text": s(), "entity_type": enum(ENTITY_TYPES), "role": enum(ROLES),
                   "reference_kind": enum(("named", "generic", "uncertain")), "evidence_quote": s()})
    metric = obj({"metric_text": s(), "value_text": s(), "value": {"type": ["number", "null"]},
                  "unit": enum(UNITS, null=True), "period_text": opt(), "direction": enum(DIRECTIONS)})
    result = obj({"id": s(), "entity_mentions": {"type": "array", "items": mention},
                  "metrics": {"type": "array", "items": metric}, "event_type": enum(EVENT_TYPES, null=True),
                  "affected_object": {"type": "array", "items": enum(AFFECTED_OBJECTS)}})
    return {"name": "claim_extractions", "strict": True,
            "schema": obj({"results": {"type": "array", "items": result}})}


# ---------- checks ----------

def checked(row: dict, text: str) -> tuple[dict, list[dict]]:
    """The fields of one result with every quote checked against the sentence; (fields, dropped
    items: what the model returned that is not part of the sentence)."""
    mentions, metrics, dropped = [], [], []
    for m in row.get("entity_mentions") or []:
        w, e = m.get("mention_text") or "", m.get("evidence_quote") or ""
        if w.strip() and w in e and e in text:
            mentions.append(dict(m))
        else:
            dropped.append({"field": "entity_mentions", "item": m})
    for x in row.get("metrics") or []:
        if (x.get("metric_text") or "") in text and (x.get("value_text") or "").strip() and x["value_text"] in text:
            x = dict(x)
            if x.get("period_text") is not None and x["period_text"] not in text:
                dropped.append({"field": "metrics.period_text", "item": dict(x)})
                x["period_text"] = None
            metrics.append(x)
        else:
            dropped.append({"field": "metrics", "item": x})
    affected = [a for a in dict.fromkeys(row.get("affected_object") or []) if a in AFFECTED_OBJECTS][:3]
    event = row.get("event_type") if row.get("event_type") in EVENT_TYPES else None
    return {"entity_mentions": mentions, "metrics": metrics, "event_type": event, "affected_object": affected}, dropped


def salvage(batch: dict, results: list[dict]) -> tuple[set[str], list[str]] | None:
    """For a reply whose ids are all batch ids but not each exactly once: (ids given once, to keep;
    ids left out or repeated, in batch order, to send again; empty when only the order differs).
    None when an id is not in the batch (nothing kept)."""
    got = Counter(r.get("id") for r in results)
    if not got or set(got) - set(batch["local_ids"]):
        return None
    again = [i for i in batch["local_ids"] if got[i] != 1]
    return {i for i, n in got.items() if n == 1}, again          # again empty: only the order differs


def check_reply(batch: dict, results: list[dict]) -> list[str]:
    got = [r.get("id") for r in results]
    if got != batch["local_ids"]:
        missing = [i for i in batch["local_ids"] if i not in got]
        extra = [i for i in got if i not in batch["local_ids"]] + [i for i, n in Counter(got).items() if n > 1]
        return [f"ids differ from the batch (missing: {', '.join(missing) or '-'}; "
                f"unknown or repeated: {', '.join(map(str, extra)) or '-'})"]
    return []


# ---------- plan ----------

def targets(units: list[dict], labels: list[dict], assume_unlabelled: bool = False) -> set[str]:
    """Unit ids that become Claims: a Claim label and not held in step 8. assume_unlabelled (dry
    run before step 7 has run): every unit step 7 would send that has no label yet counts too."""
    held = held_ids()
    claim = {l["unit_id"] for l in labels if l["label"] in CLAIM_TYPES and l["unit_id"] not in held}
    if assume_unlabelled:
        labelled = {l["unit_id"] for l in labels}
        claim |= {u["unit_id"] for u in units if u["prefilter"] is None and u["unit_id"] not in labelled}
    return claim


def estimate(plans: list[dict], cfg: dict) -> tuple[float, float]:
    """(typical, worst case) USD: input about 4 bytes a token; output answer_tokens_per_sentence
    a sentence (typical) or max_completion_tokens a request (worst)."""
    p_in, p_out = cfg.get("price_input_per_1m"), cfg.get("price_output_per_1m")
    if p_in is None or p_out is None:
        return float("nan"), float("nan")
    inp = sum(len(canonical(p["request"]).encode("utf-8")) / 4 for p in plans)
    typical = sum(len(p["batch"]["unit_ids"]) for p in plans) * cfg["answer_tokens_per_sentence"]
    if is_reasoning(cfg["model"]):            # the request's max_completion_tokens already has the room
        typical += REASONING_GUESS.get(cfg.get("reasoning_effort") or "none", 0) * len(plans)
    worst = sum(p["request"]["max_completion_tokens"] for p in plans)
    return (inp * p_in + typical * p_out) / 1e6, (inp * p_in + worst * p_out) / 1e6


# ---------- run ----------

def rows_from(plan: dict, record: dict, results: list[dict], texts: dict[str, str],
              cfg: dict) -> tuple[list[dict], list[dict]]:
    to_unit = dict(zip(plan["batch"]["local_ids"], plan["batch"]["unit_ids"]))
    out, dropped = [], []
    for r in results:
        uid = to_unit[r["id"]]
        fields, d = checked(r, texts[uid])
        dropped += [dict(x, unit_id=uid, text=texts[uid], request_hash=plan["request_hash"]) for x in d]
        out.append({"unit_id": uid, **fields, "source": "llm", "request_hash": plan["request_hash"],
                    "model": record["raw_response"].get("model"), "prompt_version": cfg["prompt_version"],
                    "runner_version": RUNNER_VERSION})
    return out, dropped


def run(dry_run: bool = False, limit: int | None = None, workers: int | None = None,
        model: str | None = None, compare: bool = False) -> None:
    cfg = config(model)
    units = list(read_jsonl(Paths.sentences()))
    labels = reviewed_labels(unit_ids={u["unit_id"] for u in units})
    claims = targets(units, labels, assume_unlabelled=dry_run)
    if dry_run and len(claims) > len({l["unit_id"] for l in labels if l["label"] in CLAIM_TYPES}):
        log(STEP, "dry run: units step 7 has not labelled yet are counted as Claims")
    # a unit a person released from a prefilter (label_overrides.csv) is sent like any Claim
    units = [dict(u, prefilter=None) if u["prefilter"] and u["unit_id"] in claims else u for u in units]
    reuse = {u: f for u, f in reuse_plan(units, cfg).items() if u in claims and f in claims}
    system, schema = system_prompt(cfg["prompt_version"]), response_schema()

    def plan(only: set[str]) -> list[dict]:
        return plan_batches(units, cfg, only=only, system=system, schema=schema, runner=RUNNER_VERSION) if only else []

    def retry_plan(only: set[str]) -> list[dict]:
        """The retry round: batches of at most RETRY_BATCH Claims, and a runner id of their own, so
        even a batch the same as before is a new request (its first reply stays saved as it was)."""
        small = dict(cfg, batch_max_sentences=min(RETRY_BATCH, cfg["batch_max_sentences"]))
        return plan_batches(units, small, only=only, system=system, schema=schema,
                            runner=RUNNER_VERSION + "/retry") if only else []

    first_round = plan(claims - set(reuse))
    typical, worst = estimate(first_round, cfg)
    log(STEP, f"{len(claims)} Claims: {len(claims) - len(reuse)} to send in {len(first_round)} requests, "
              f"{len(reuse)} copy an earlier filing's extraction; about ${typical:.2f} typical, "
              f"${worst:.2f} worst case ({cfg['model']})")
    if compare:
        from .llm.cost import compare_models
        compare_models(STEP, first_round, cfg, [], log, estimator=estimate)
    if dry_run:
        return

    texts = {u["unit_id"]: u["text"] for u in units}
    client = Client(cfg, usage_path=Paths.step_usage(STEP))
    state = {"sent": 0, "dropped": [], "rejected": [], "stopped": None, "left_out": set()}
    rows: dict[str, dict] = {}

    def one(p: dict, send: bool):
        record = client.cached(p, KIND)
        if record is None and send:
            record = client.send(p, KIND)
            return record, True
        return record, False

    def handle(p: dict, record: dict | None) -> None:
        if record is None:
            return
        hard, parsed = parse_reply(record["raw_response"], schema["schema"], "results")
        problems = hard or check_reply(p["batch"], parsed["results"])
        # Every row the model gave is a batch id: keep the rows of ids given once; ids left out or
        # given more than once are sent again in the retry round (none: only the order differs).
        split = salvage(p["batch"], parsed["results"]) if problems and not hard else None
        if split:
            keep, again = split
            parsed = {"results": [r for r in parsed["results"] if r["id"] in keep]}
        if split and again:
            to_unit = dict(zip(p["batch"]["local_ids"], p["batch"]["unit_ids"]))
            missing = [to_unit[n] for n in again]
            log(STEP, f"{p['batch']['batch_id']}: {len(missing)} of {len(p['batch']['unit_ids'])} Claims "
                      f"left out of the reply or repeated in it; kept the rest, those are sent again")
            state["rejected"].append({"batch_id": p["batch"]["batch_id"], "request_hash": p["request_hash"],
                                      "problems": problems, "unit_ids": missing, "kept_the_rest": True,
                                      "sent_again": True})
            state["left_out"].update(missing)
        if split:
            problems = []
        if hard and hard[0].startswith("Missing or incomplete completion"):
            # cut off at max_completion_tokens (a reasoning model can spend it all on reasoning):
            # every Claim of the batch is sent again in the retry round, in smaller batches
            log(STEP, f"{p['batch']['batch_id']}: reply cut off; its {len(p['batch']['unit_ids'])} Claims "
                      f"are sent again in smaller batches")
            state["rejected"].append({"batch_id": p["batch"]["batch_id"], "request_hash": p["request_hash"],
                                      "problems": problems, "unit_ids": p["batch"]["unit_ids"], "sent_again": True})
            state["left_out"].update(p["batch"]["unit_ids"])
            return
        if problems:
            log(STEP, f"{p['batch']['batch_id']} rejected: {'; '.join(problems)[:200]}")
            state["rejected"].append({"batch_id": p["batch"]["batch_id"], "request_hash": p["request_hash"],
                                      "problems": problems, "unit_ids": p["batch"]["unit_ids"]})
            if cfg.get("stop_on_failure", True):
                state["stopped"] = p["batch"]["batch_id"]
            return
        new, dropped = rows_from(p, record, parsed["results"], texts, cfg)
        state["dropped"] += dropped
        rows.update((r["unit_id"], r) for r in new)

    def send_all(plans: list[dict]) -> None:
        cached = [(p, client.cached(p, KIND)) for p in plans]
        for p, record in cached:
            handle(p, record)
        todo = [p for p, record in cached if record is None]
        if limit is not None:
            todo = todo[:max(0, limit - state["sent"])]
        n = max(1, int(workers or cfg.get("concurrency") or 1))
        bar = Progress(STEP, len(todo), "requests", spent=lambda: client.spent)
        try:
            with ThreadPoolExecutor(max_workers=n) as pool:
                for p, (record, sent) in zip(todo, pool.map(lambda q: one(q, not state["stopped"]), todo)):
                    state["sent"] += sent
                    bar.tick()
                    handle(p, record)
        except StopRun as err:
            state["stopped"] = str(err)
            log(STEP, f"stopped: {err}")
        finally:
            bar.close()

    def send_again() -> None:
        """One more round for Claims left out of a reply or cut off, in new (smaller) batches."""
        retry = state["left_out"] - set(rows)
        state["left_out"] = set()
        if retry and not state["stopped"]:
            log(STEP, f"{len(retry)} Claims left out of, repeated in or cut off from their batch's reply: sent again")
            send_all(retry_plan(retry))

    send_all(first_round)
    send_again()
    copied = 0
    if reuse:
        waiting = {u for u, f in reuse.items() if f not in rows}
        for uid, first in reuse.items():
            if first in rows:
                rows[uid] = dict(rows[first], unit_id=uid, source="llm_reused", reused_from_unit_id=first)
                copied += 1
        if waiting and not state["stopped"]:
            send_all(plan(waiting))
            send_again()
    order = {u["unit_id"]: k for k, u in enumerate(units)}
    out = sorted(rows.values(), key=lambda r: order[r["unit_id"]])
    write_jsonl(Paths.extractions(), out)
    folder = checks_dir(STEP)
    folder.mkdir(parents=True, exist_ok=True)
    write_jsonl(folder / "dropped.jsonl", state["dropped"])
    write_jsonl(folder / "rejected_batches.jsonl", state["rejected"])
    write_jsonl(folder / "missing.jsonl", [{"unit_id": u} for u in sorted(claims - set(rows), key=order.get)])
    log(STEP, f"{state['sent']} new requests; {len(out)}/{len(claims)} Claims extracted ({copied} copied), "
              f"{len(state['dropped'])} quoted fields not in their sentence dropped, "
              f"{len(state['rejected'])} batches rejected -> {Paths.extractions()}; review: {folder}")
    if state["stopped"]:
        log(STEP, f"stopped at {state['stopped']}; the rest wait for the next run")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="plan and estimate cost only")
    parser.add_argument("--limit", type=int, help="send at most this many new requests")
    parser.add_argument("--workers", type=int, help="requests in flight at once (default extract.concurrency)")
    parser.add_argument("--model", help="use this model instead of settings.yaml extract.model (a trial)")
    parser.add_argument("--compare", action="store_true", help="dry run: cost with every model in settings prices:")
    args = parser.parse_args()
    run(dry_run=args.dry_run or args.compare, limit=args.limit, workers=args.workers, model=args.model,
        compare=args.compare)


if __name__ == "__main__":
    main()
