"""Try step 4b (heading levels) with several models on a few sections, side by side.

  python -m src.compare_heading_levels --list                       sections with sub-headings
  python -m src.compare_heading_levels --list --grep MSFT           ...only those matching MSFT
  python -m src.compare_heading_levels \\
      --pick "MSFT|10-K|part-ii-item-7" --pick "AAPL|10-K|part-i-item-1" --pick "CASY|10-K|part-i-item-1a" \\
      --config gpt-4.1-mini --config gpt-5.4-mini:none --config gpt-5.4-mini:low --dry-run
  (the same without --dry-run sends the requests)

--pick is pieces separated by "|", each equal to the ticker, form, accession or section_key of
the section (so part-i-item-1 does not match part-i-item-1a); the latest matching filing is
used, add the accession to pick another. --config is MODEL or MODEL:REASONING_EFFORT.

Requests go through the same code, folder and ledger as step 4b (data/heading_levels/,
data/output/usage_heading_levels.jsonl, heading_levels.budget_usd), so whatever you try here is
already paid for when step 4b runs later with the same config.

Writes data/interim/heading_level_compare.csv: one row per heading, one level column per config,
`differs` = the configs disagree, and an empty `correct` column to fill in by hand. Rerunning
costs nothing for configs already sent; with `correct` filled in, it prints each config's score.
"""

from __future__ import annotations

import argparse
import csv

from . import heading_levels as H
from . import llm_cost
from .common import Paths, data_dir, log

STEP = "heading_levels"
OUT = lambda: data_dir() / "interim" / "heading_level_compare.csv"  # noqa: E731


def key(sec: dict) -> str:
    return f"{sec['ticker']}|{sec['form']}|{sec['accession']}|{sec['section_key']}|{sec['section']}"


def pick(sections: list[dict], patterns: list[str]) -> list[dict]:
    chosen = []
    for pat in patterns:
        want = {x.strip().lower() for x in pat.split("|") if x.strip()}
        hits = [s for s in sections
                if want <= {str(s[f]).lower() for f in ("ticker", "form", "accession", "section_key")}]
        if not hits:
            raise SystemExit(f"--pick {pat!r} matches no section; try --list --grep")
        chosen.append(hits[-1])              # filings are in period order: the latest
    return chosen


def config(spec: str) -> dict:
    model, _, effort = spec.partition(":")
    cfg = llm_cost.step_cfg(STEP, model)
    if llm_cost.is_reasoning(model):
        cfg["reasoning_effort"] = effort or cfg.get("reasoning_effort")
        # max_reasoning_tokens stays as in settings even for "none", so the request (and its
        # hash) is the one step 4b will send with the same model and effort.
    elif effort:
        raise SystemExit(f"{model} is not a reasoning model: drop ':{effort}'")
    return cfg


def scores(rows: list[dict], specs: list[str]) -> None:
    graded = [r for r in rows if (r.get("correct") or "").strip()]
    if not graded:
        log(STEP, f"fill in the `correct` column of {OUT()} and rerun to score the configs")
        return
    for spec in specs:
        right = sum(r.get(spec, "") == r["correct"].strip() for r in graded)
        log(STEP, f"{spec:<22} {right}/{len(graded)} correct")


def run(patterns: list[str], specs: list[str], dry_run: bool) -> None:
    from .label import Labeller, StopRun
    sections = pick(H.section_headings(), patterns)
    n_heads = sum(len(s["headings"]) for s in sections)
    log(STEP, f"{len(sections)} sections, {n_heads} headings: " + "; ".join(key(s) for s in sections))
    levels: dict[str, dict[str, int]] = {}
    for spec in specs:
        cfg = config(spec)
        todo = H.plans(sections, cfg)
        e = llm_cost.estimate(todo, cfg, H.expected_answer_tokens(todo))
        labeller = Labeller(cfg, Paths.step_usage(STEP))
        new = [p for p in todo if labeller.cached(p, STEP) is None]
        log(STEP, f"{spec}: {len(todo)} requests ({len(new)} not yet sent), typical about "
                  f"${e['typical']:.3f}, worst ${e['worst']:.3f}")
        if dry_run:
            continue
        got: dict[str, int] = {}
        try:
            for plan in todo:
                record = labeller.cached(plan, STEP) or labeller.send(plan, STEP)
                problems, answer = H.answers(plan, record)
                for p in problems:
                    log(STEP, f"  {spec} {plan['batch']['batch_id']}: {p}")
                got.update(answer)
        except StopRun as exc:
            log(STEP, f"  {spec} stopped: {exc}")
        levels[spec] = got
    if dry_run:
        return

    old = {}
    if OUT().exists():                     # keep the `correct` column you filled in
        with open(OUT(), encoding="utf-8-sig", newline="") as f:
            old = {r["heading_id"]: r for r in csv.DictReader(f)}
    rows = []
    for sec in sections:
        for h in sec["headings"]:
            row = {"section": key(sec), "heading_id": h["heading_id"], "text": h["text"],
                   "style": h["style"], "text_below": h["next_text"]}
            answers = [levels[s].get(h["heading_id"]) for s in specs]
            row.update({s: "" if a is None else a for s, a in zip(specs, answers)})
            row["differs"] = "yes" if len({a for a in answers if a is not None}) > 1 else ""
            row["correct"] = old.get(h["heading_id"], {}).get("correct", "")   # keep your grading
            rows.append(row)
    OUT().parent.mkdir(parents=True, exist_ok=True)
    with open(OUT(), "w", newline="", encoding="utf-8-sig") as f:      # -sig: Excel opens it as UTF-8
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    log(STEP, f"{sum(r['differs'] == 'yes' for r in rows)} of {len(rows)} headings differ between configs -> {OUT()}")
    scores([{k: str(v) for k, v in r.items()} for r in rows], specs)


def list_sections(grep: str | None) -> None:
    for s in H.section_headings():
        if not grep or grep.lower() in key(s).lower():
            print(f"{len(s['headings']):>4} headings  {key(s)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="list sections with sub-headings")
    ap.add_argument("--grep", help="with --list: only sections whose key contains this")
    ap.add_argument("--pick", action="append", default=[], help="section to try (repeat)")
    ap.add_argument("--config", action="append", default=[], help="MODEL or MODEL:EFFORT (repeat)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.list:
        list_sections(args.grep)
    elif not args.pick or not args.config:
        ap.error("give --pick and --config (or --list)")
    else:
        run(args.pick, args.config, args.dry_run)
