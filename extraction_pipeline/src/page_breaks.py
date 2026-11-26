"""Step 2b - Page breaks: ask the LLM yes/no for the paragraph boundaries step 2 cannot decide.

Step 2 (clean.py) gives every boundary between two paragraphs a class (clean.boundary_class).
Most classes decide the join by rule. Two are gray (clean.GRAY_CLASSES):
  C9_gray_no_punct_capital  a prose paragraph with no end punctuation, the next starts upper case
                            ("...including, the European Union" / "General Data Protection ...")
  C9_gray_short_prev_lower  a paragraph under 8 words, the next starts lower case
                            ("Earnings (loss) per common share attributable" / "to McKesson")
This step sends only those, in kept sections, to the model: "is B the same sentence as A, cut by a
page break?" The answers go to data/interim/page_break_decisions.jsonl, and step 2 joins a gray
boundary only when the answer is yes. Without the file, or for a boundary it does not list,
nothing changes: gray boundaries are not joined, as before.

Usage:
  python -m src.page_breaks --dry-run     list the boundaries and the estimated cost, no API calls
  python -m src.page_breaks               ask, then rerun steps 2-4 (python run_pipeline.py --from clean)
  python -m src.page_breaks --limit 3     send at most 3 new requests

Money rules are label.py's: a request whose reply is saved (data/page_breaks/<hash>/) is never
sent again, nothing is resent automatically, and the run stops before budget_usd. The model,
reasoning effort and budget are this step's own (settings.yaml page_breaks:), and its spend is
logged to data/output/usage_page_breaks.jsonl, separate from step 5.
  python -m src.page_breaks --dry-run --compare        cost with every model in settings prices:
  python -m src.page_breaks --model gpt-5.5 --limit 1  try another model than the configured one
A boundary's id is accession|section_key|start of the second paragraph in the Data-team content
(clean.boundary_id), so a decision survives a change of the cleaning rules elsewhere.
"""

from __future__ import annotations

import argparse

from . import clean as C
from . import llm_cost
from .common import Paths, filings, load_config, log, read_jsonl, settings, write_jsonl
from .filter_sections import decide

STEP = "page_breaks"
RUNNER_VERSION = "kelly_page_breaks_v2"      # v2: one section per request
TAIL = 300                      # characters of each paragraph the model sees
BATCH = 20                      # boundaries per request, at most; one section per request

SYSTEM = """You repair text extracted from SEC 10-K and 10-Q filings. A page break can cut a
sentence in two, so the end of one paragraph and the start of the next are one sentence.
For each pair, A is the end of a paragraph and B the start of the next paragraph.
Answer same_sentence = true only when B continues the sentence that A leaves unfinished
(for example A "...under Subpart F of the Internal" and B "Revenue Code, the Company recognized").
Answer false when A is complete as it stands (a heading, a title, a list item, a sentence whose
period was lost) and B starts something new. When unsure, answer false.
Return one result per pair id, in any order."""


def decisions_path():
    return Paths.page_break_decisions()


def load_decisions() -> dict[str, bool]:
    """boundary id -> join, from step 2b; empty when the step has not run."""
    path = decisions_path()
    if not path.exists():
        return {}
    return {r["boundary_id"]: bool(r["same_sentence"]) for r in read_jsonl(path)}


def gray_boundaries() -> list[dict]:
    """Every gray boundary in a section step 3 keeps (or marks review), in source order."""
    cfg, rules = settings()["clean"], load_config("sections")
    out = []
    for f in filings():
        path = Paths.sections(f)
        if not path.exists():
            continue
        sections = list(read_jsonl(path))
        headers = C.repeated_headers(sections, cfg)
        for sec in sections:
            if not (sec.get("content") or "").strip():
                continue
            if decide(C.clean_section(sec, headers), rules)[0] == "drop":
                continue
            for b in C.walk(sec, headers)[2]:
                if b["class"] in C.GRAY_CLASSES:
                    out.append({"boundary_id": C.boundary_id(sec, b["next_start"]), "class": b["class"],
                                "ticker": f["ticker"], "form": f["form"], "section_type": sec.get("section_type"),
                                "a": b["prev"][-TAIL:].translate(C.ONE_FOR_ONE),
                                "b": b["next"][:TAIL].translate(C.ONE_FOR_ONE)})
    return out


def schema() -> dict:
    row = {"type": "object", "additionalProperties": False, "required": ["id", "same_sentence"],
           "properties": {"id": {"type": "string"}, "same_sentence": {"type": "boolean"}}}
    return {"name": "page_breaks", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": ["results"],
                       "properties": {"results": {"type": "array", "items": row}}}}


def plans(boundaries: list[dict], cfg: dict) -> list[dict]:
    """Requests of up to BATCH boundaries, never mixing two sections.

    A request holds the boundaries of one section only, so adding, removing or changing a section
    leaves every other section's request (and its saved reply) as it was.
    """
    from .label import canonical, digest
    by_section: dict[str, list[dict]] = {}
    for b in boundaries:                          # boundary_id = accession|section_key|start
        by_section.setdefault(b["boundary_id"].rsplit("|", 1)[0], []).append(b)
    out = []
    for section, items in by_section.items():
        for part, i in enumerate(range(0, len(items), BATCH)):
            chunk = items[i:i + BATCH]
            pairs = [{"id": f"p{k + 1}", "A": b["a"], "B": b["b"]} for k, b in enumerate(chunk)]
            request = {"model": cfg["model"],
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": canonical({"pairs": pairs})}],
                       "response_format": {"type": "json_schema", "json_schema": schema()},
                       "store": False}
            llm_cost.model_params(request, cfg, answer_tokens=40 * len(chunk) + 200)
            key = {"runner_version": RUNNER_VERSION, "request": request}
            batch = {"batch_id": f"{section}|page_breaks_{part}", "boundary_ids": [b["boundary_id"] for b in chunk]}
            out.append({"batch": batch, "request": request, "request_hash": digest(canonical(key)),
                        "identity": {"runner_version": RUNNER_VERSION, "request": request, "batch": batch}})
    return out


def answers(plan: dict, record: dict) -> tuple[list[str], dict[str, bool]]:
    """Problems with a reply, and boundary id -> same_sentence for the ids it answered once."""
    from .label import parse_reply
    problems, parsed = parse_reply(record["raw_response"], schema()["schema"], "results",
                                   duplicate_keys_ok=False)
    if problems:
        return problems, {}
    ids = plan["batch"]["boundary_ids"]
    by_local = {f"p{k + 1}": b for k, b in enumerate(ids)}
    seen: dict[str, list[bool]] = {}
    for row in parsed["results"]:
        if row["id"] in by_local:
            seen.setdefault(row["id"], []).append(row["same_sentence"])
    missing = sorted(set(by_local) - set(seen))
    if missing:
        problems.append(f"no answer for {', '.join(missing)} (left unjoined)")
    return problems, {by_local[k]: v[0] for k, v in seen.items() if len(v) == 1}


def expected_answer_tokens(todo: list[dict]) -> list[int]:
    """About 12 tokens per pair ({"id":"p12","same_sentence":false},) plus the envelope."""
    return [12 * len(p["batch"]["boundary_ids"]) + 10 for p in todo]


def run(dry_run: bool = False, limit: int | None = None, model: str | None = None,
        compare: bool = False) -> None:
    from .label import Labeller, StopRun
    cfg = llm_cost.step_cfg(STEP, model)
    boundaries = gray_boundaries()
    todo = plans(boundaries, cfg)
    log(STEP, f"{len(boundaries)} gray boundaries in kept sections, model {cfg['model']}"
              + (f", reasoning_effort {cfg.get('reasoning_effort')}" if llm_cost.is_reasoning(cfg["model"]) else ""))
    llm_cost.report(STEP, cfg, lambda c: plans(boundaries, c), expected_answer_tokens, log, compare)
    if dry_run:
        write_jsonl(Paths.page_break_candidates(), boundaries)
        return
    labeller, sent, decided = Labeller(cfg, Paths.step_usage(STEP)), 0, {}
    try:
        for plan in todo:
            record = labeller.cached(plan, "page_breaks")
            if record is None:
                if limit is not None and sent >= limit:
                    continue
                record = labeller.send(plan, "page_breaks")
                sent += 1
            problems, got = answers(plan, record)
            for p in problems:
                log(STEP, f"{plan['batch']['batch_id']}: {p}")
            decided.update(got)
    except StopRun as exc:
        log(STEP, f"stopped: {exc}")
    rows = [{"boundary_id": b["boundary_id"], "class": b["class"], "same_sentence": decided[b["boundary_id"]],
             "a_tail": b["a"][-80:], "b_head": b["b"][:80], "runner_version": RUNNER_VERSION, "model": cfg["model"]}
            for b in boundaries if b["boundary_id"] in decided]
    write_jsonl(decisions_path(), rows)
    log(STEP, f"{sent} requests sent; {len(rows)} of {len(boundaries)} answered, "
              f"{sum(r['same_sentence'] for r in rows)} joined -> {decisions_path()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", help="use this model instead of settings.yaml page_breaks.model")
    ap.add_argument("--compare", action="store_true", help="cost with every model in settings prices:")
    args = ap.parse_args()
    run(dry_run=args.dry_run or args.compare, limit=args.limit, model=args.model, compare=args.compare)
