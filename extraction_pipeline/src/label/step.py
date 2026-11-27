"""Step 7 - Label: the LLM labels each sentence (OpenAI).

Follows Claudia's checked_pilot.py: the same request layout, identity fields and cache folders
as her run.

Request hash: the cache key, made from what decides the model's reply only: the request sent
(model, prompt, sentences, schema, limits) and the runner version (request_key()). The full
identity (the request plus the units, source versions, segment names and entity catalog it
was built from) is still saved in request.json as the record, but is not hashed. So a change
that does not reach the model (a new field on the units, a filled entity catalog, a re-export
with the same text) reuses the saved replies. Before 4 November 2026 the whole identity was
hashed, as in Claudia's run.

Input : data/output/sentences.jsonl, data/sections/*.jsonl, config/claim_types.yaml,
        prompts/<prompt_version>.txt, data/entity_catalog.json (optional)
Output: data/output/labels.jsonl         one label per unit (LLM, code review or prefilter rule)
        data/output/usage.jsonl          one usage record per API request
        data/interim/label_batches.jsonl the planned batches
        data/checked_pilot/<hash>/       one folder per batch request:
            request.json          the full identity: what was sent, and what it was built from
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
  One exception: a rate-limit reply (HTTP 429, not "insufficient_quota") is refused before
  any generation and is not billed, so it is sent again after a wait, at most
  rate_limit_retries times.
- The run stops before a request could take total estimated spend past budget_usd. With
  concurrency > 1, requests in flight count at their worst-case cost until their reply is in.

Concurrency (label.concurrency, or --workers): that many requests are in flight at once. A
request whose reply is saved is read from the cache as before; two batches with the same
request never fly together (the second waits and then finds the first one's reply).

A reply is rejected, and publishes no labels, when it is incomplete (finish_reason is not
"stop"), a refusal, not exactly {"results": [...]}, or off the JSON schema. By default the
run then stops (stop_on_failure); max_retries > 0 sends a follow-up request listing the problems.

Ids: every stored record names a unit by its unit_id. The model sees short numbers instead,
counted within the request: "c1", "c2", ... for the context units and "s1" ... "s15" for the
sentences to label (batch_numbers()). They are made when a request is built, appear only in
the saved request and reply, and are turned back into unit_ids as soon as a reply is read;
labels.jsonl has unit_id and context_unit_ids. Because they count within the request, a
change elsewhere in the section that leaves a batch's own units and context alone (a heading
or page footer added or removed further up) leaves its request, and its saved reply, as is.

Context: the context_sentences units before a batch (or a repair target), plus the intro
sentence of any split list item in it whose intro is neither in the batch nor in those units
(list_intros()), in source order. So an item of a long list cut by a batch boundary still
sees "... include:" (README Remaining issues 2.3, fixed 4 November 2026).

Problems with single units are handled as in Claudia's run (her recovery v2 and repair scripts):
- Unit quarantine: the rest of the reply is kept. A unit with invalid context ids, or whose
  id is missing or repeated, becomes needs_review (source "code_quarantine", no Claim). Rows
  with ids not in the batch are ignored. Nothing is guessed from row order.
- Targeted repair: once every batch has a reply, units whose id was missing or repeated are
  sent again in one request, each under a key assigned by code (repair_1, repair_2, ...),
  with its own context (its 4 preceding units, plus its list intro when missing). Their labels
  replace the placeholders.

Repeats (label.reuse_repeats: path): a unit whose text, 4 preceding units and section_path are
the same as a unit in an earlier filing of the same company (checks/unit_repeats.py, level
"path") is not sent. It gets a copy of that first occurrence's label (source "llm_reused",
reused_from_unit_id), with context_unit_ids moved to its own units at the same distance before
it. Batches are cut at reused units, so every sent sentence still has its own preceding units
as context. A reused unit falls back to its own request (a second round of batches in the same
run) when the first occurrence has no model label (held by quarantine) or cites a context unit
whose counterpart before the reused unit has other text. Set reuse_repeats to null to label
every occurrence.

Usage:
  python -m src.label --dry-run                         plan batches, estimate cost, no API calls
  python -m src.label --filing 0000320193-25-000079     label one filing first
  python -m src.label --limit 3                         send at most 3 new requests
  python -m src.label --workers 8                       8 requests in flight at once
  python -m src.label --report                          print token and cost totals
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict

from ..common import Paths, log, read_jsonl, settings, write_jsonl
from ..llm.client import StopRun, reserve_cost
from .repair import plan_repair, repair_labels
from .replies import Labeller
from .requests import RUNNER_VERSION, STEP, plan_batches
from .reuse import reuse_plan, reused_labels


# ---------- labels file ----------

def rule_label(unit: dict) -> dict:
    return {"unit_id": unit["unit_id"], "label": "not_a_claim", "source": f"rule:{unit['prefilter']}",
            "context_unit_ids": [], "subject_scope": None, "annotation_note": None, "is_risk_heading": False,
            "entity_mentions": []}


def llm_labels(plan: dict, record: dict, results: list[dict], held: list[dict], cfg: dict) -> list[dict]:
    """Labels from an accepted reply, with the model's short numbers turned back into unit ids."""
    batch = plan["batch"]
    to_unit = dict(zip(batch["context_local_ids"] + batch["local_ids"], batch["context_unit_ids"] + batch["unit_ids"]))
    held_ids = {h["unit_id"] for h in held}
    return [{
        "unit_id": to_unit[r["id"]], "label": r["label"],
        "source": "code_quarantine" if to_unit[r["id"]] in held_ids else "llm",
        "context_unit_ids": [to_unit[c] for c in r["context_ids"]],
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


def run(dry_run: bool = False, filing: str | None = None, limit: int | None = None,
        workers: int | None = None) -> None:
    cfg = settings()["label"]
    units = list(read_jsonl(Paths.sentences()))
    reuse = reuse_plan(units, cfg)
    plans = plan_batches(units, cfg, skip=set(reuse))
    write_jsonl(Paths.batches(), [dict(p["batch"], request_hash=p["request_hash"]) for p in plans])
    if reuse:
        log(STEP, f"{len(reuse)} units reuse the label of an earlier filing (reuse_repeats: "
                  f"{cfg['reuse_repeats']}); they are not sent")

    if dry_run:
        reserve = sum(reserve_cost(p["request"], cfg) for p in plans)
        log(STEP, f"{len(plans)} batches, {sum(len(p['batch']['unit_ids']) for p in plans)} sentences; "
                  f"worst-case cost ${reserve:.2f} (dry run, no API calls)")

    if cfg.get("price_input_per_1m") is None or cfg.get("price_output_per_1m") is None:
        log(STEP, "warning: token prices are not set in config/settings.yaml, so costs are not "
                  "estimated and budget_usd is not enforced")
    labeller = Labeller(cfg)
    workers = max(1, int(workers or cfg.get("concurrency") or 1))
    if workers > 1 and not dry_run:
        log(STEP, f"{workers} requests in flight at once")
    labels = [rule_label(u) for u in units if u["prefilter"]]
    state = {"sent": 0, "rejected": 0, "complete": 0, "stopped": None}
    gaps: dict[str, str] = {}              # unit id -> "missing" or "ambiguous"

    def may_send(plan: dict, in_flight: int = 0) -> bool:
        selected = filing is None or plan["batch"]["accession_number"] == filing
        return (not dry_run and not state["stopped"] and selected
                and (limit is None or state["sent"] + in_flight < limit))

    def handle(plan: dict, reply: dict) -> None:
        nonlocal labels
        state["sent"] += reply["sent"]
        if reply["record"] is None:
            return
        if reply["problems"]:
            state["rejected"] += 1
            log(STEP, f"{plan['batch']['batch_id']} rejected: {'; '.join(reply['problems'])[:200]}")
            if cfg["stop_on_failure"] and not state["stopped"]:
                state["stopped"] = f"{plan['batch']['batch_id']} rejected; see {labeller.folder(plan['request_hash'])}"
                log(STEP, f"stopped: {state['stopped']}")
            return
        state["complete"] += 1
        labels += llm_labels(plan, reply["record"], reply["results"], reply["held"], cfg)
        for h in reply["held"]:
            log(STEP, f"{plan['batch']['batch_id']} {h['local_id']} set aside as needs_review: "
                      f"{', '.join(h['review_flags'])}")
            if h["review_flags"][0] in ("missing_model_target_id", "duplicate_model_target_id"):
                gaps[h["unit_id"]] = "missing" if h["review_flags"][0] == "missing_model_target_id" else "ambiguous"

    def stop(err: StopRun) -> None:
        if not state["stopped"]:
            state["stopped"] = str(err)
        log(STEP, f"stopped: {err}")

    def label_all(batch_plans: list[dict]) -> None:
        if workers <= 1:
            for plan in batch_plans:
                try:
                    handle(plan, labeller.label(plan, send=may_send(plan)))
                except StopRun as err:
                    stop(err)
            return
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        queue, in_flight, busy, waiting = list(batch_plans), {}, set(), defaultdict(list)
        last_log = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while queue or in_flight:
                while queue and len(in_flight) < workers:
                    plan = queue.pop(0)
                    if plan["request_hash"] in busy:            # same request already flying
                        waiting[plan["request_hash"]].append(plan)
                        continue
                    try:
                        reply = labeller.label(plan, send=False)          # cache only
                    except StopRun as err:
                        stop(err)
                        continue
                    if reply["record"] is not None or not may_send(plan, len(in_flight)):
                        handle(plan, reply)
                        continue
                    busy.add(plan["request_hash"])
                    in_flight[pool.submit(labeller.label, plan, True)] = plan
                if not in_flight:
                    continue
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in finished:
                    plan = in_flight.pop(future)
                    busy.discard(plan["request_hash"])
                    queue.extend(waiting.pop(plan["request_hash"], []))   # they now find the reply
                    try:
                        handle(plan, future.result())
                    except StopRun as err:
                        stop(err)
                if time.perf_counter() - last_log > 60:
                    last_log = time.perf_counter()
                    log(STEP, f"{state['sent']} sent, {len(in_flight)} in flight, about ${labeller.spent:.2f} so far"
                              + (f", {labeller.rate_limited} rate-limit waits" if labeller.rate_limited else ""))

    label_all(plans)
    if reuse:
        # Second round: reused units whose first occurrence gave no label they can copy.
        copies, fallback = reused_labels(labels, reuse, units)
        second = plan_batches(units, cfg, only=fallback) if fallback else []
        if second:
            log(STEP, f"{len(fallback)} reused units cannot copy their first occurrence's label; "
                      f"labelled on their own in {len(second)} batches")
            write_jsonl(Paths.batches(), [dict(p["batch"], request_hash=p["request_hash"]) for p in plans + second])
            label_all(second)
        plans = plans + second
        labels += copies
        waiting = len(reuse) - len(copies) - len(fallback)
        log(STEP, f"{len(copies)} labels reused" + (f"; {waiting} wait for their first occurrence's label"
                                                    if waiting else ""))
    sent, rejected, complete, stopped = state["sent"], state["rejected"], state["complete"], state["stopped"]

    if gaps and complete == len(plans):
        order = {u["unit_id"]: i for i, u in enumerate(units)}
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
                    done = {l["unit_id"] for l in repaired}
                    labels = [l for l in labels if l["unit_id"] not in done] + repaired
                    log(STEP, f"repaired {len(repaired)} units in one request")
        except StopRun as err:
            log(STEP, f"stopped: {err}")
    elif gaps:
        log(STEP, f"{len(gaps)} units wait for the repair request, sent once every batch has a reply")

    order = {u["unit_id"]: i for i, u in enumerate(units)}
    labels.sort(key=lambda l: order.get(l["unit_id"], 1 << 30))
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
    parser.add_argument("--workers", type=int, help="requests in flight at once (default label.concurrency)")
    args = parser.parse_args()
    if args.report:
        usage_report()
        return
    run(dry_run=args.dry_run, filing=args.filing, limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
