"""Repeated sentences across filings of one company, on step 4's units, before labelling.

  python -m src.report_unit_repeats          read data/output/sentences.jsonl, write data/checks/unit_repeats/

Only units step 5 would send to the model (prefilter is None) are counted. A repeat is the same
unit in two or more filings of the same company; repeats inside one filing only are left out.
Three levels, each stricter than the one before:

  text          the sentence itself (whitespace normalised), as in report_claim_repeats.py
  context       the sentence and the 4 units before it (step 5's context_sentences: section
                titles left out, headings and prefiltered units kept)
  path          the sentence, those 4 units, and section_path

`context` is the middle level because section_path is not reliable yet (heading levels are
inferred from plain text): two units with the same text and context but different paths are
most likely the same passage with a heading read differently.

Outputs (data/checks/unit_repeats/):
  unit_repeats.jsonl    one row per counted unit: its group id and first occurrence at each level
                        (null when the unit is not repeated at that level), to join on unit_id
  summary.csv           per level, company and section type: units, repeated units, groups, and
                        reusable units (repeated units minus the first occurrence of each group)
  samples.csv           examples where the levels disagree: same text but other context, and
                        same text and context but another section_path
No API calls; nothing in the pipeline reads these files yet.
"""

from __future__ import annotations

import csv
import hashlib
import random
from collections import Counter, defaultdict

from .common import Paths, checks_dir, filings, log, read_jsonl, settings, write_jsonl

STEP = "repeats"
LEVELS = ("text", "context", "path")


def norm(text: str) -> str:
    return " ".join((text or "").split())


def out_dir():
    return checks_dir("unit_repeats")


def unit_keys(units: list[dict], context_size: int) -> list[tuple[dict, dict[str, tuple]]]:
    """(unit, {level: key}) for every unit step 5 would label, in source order."""
    by_section: dict[tuple, list[dict]] = defaultdict(list)
    for u in units:
        by_section[(u["accession_number"], u["section_key"])].append(u)
    out = []
    for section_units in by_section.values():
        before: list[str] = []                    # texts of the units before, titles left out
        for u in section_units:
            if u["prefilter"] is None:
                text = norm(u["text"])
                context = tuple(before[-context_size:]) if context_size else ()
                company = u["ticker"]
                out.append((u, {"text": (company, text),
                                "context": (company, text, context),
                                "path": (company, text, context, u["section_path"])}))
            if u["role"] != "section_title":
                before.append(norm(u["text"]))
    return out


def group_id(level: str, key: tuple) -> str:
    return f"{level}:" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:16]


def build(units: list[dict], context_size: int, filing_order: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Rows of unit_repeats.jsonl and of summary.csv."""
    keyed = unit_keys(units, context_size)
    order = lambda u: (filing_order.get(u["accession_number"], 10**9), u["accession_number"], u["char_start"])  # noqa: E731
    rows = {u["unit_id"]: {"unit_id": u["unit_id"], "ticker": u["ticker"], "accession_number": u["accession_number"],
                           "section_type": u["section_type"]} for u, _ in keyed}
    summary = Counter()
    for level in LEVELS:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for u, keys in keyed:
            groups[keys[level]].append(u)
        for key, members in groups.items():
            repeated = len({m["accession_number"] for m in members}) >= 2
            first = min(members, key=order) if repeated else None
            for m in members:
                row = rows[m["unit_id"]]
                row[f"{level}_group"] = group_id(level, key) if repeated else None
                row[f"{level}_first_unit_id"] = first["unit_id"] if repeated else None
                c = (level, m["ticker"], m["section_type"])
                summary[c + ("units",)] += 1
                if repeated:
                    summary[c + ("repeated_units",)] += 1
                    summary[c + ("reusable_units",)] += m is not first
            if repeated:
                for c in {(level, m["ticker"], m["section_type"]) for m in members}:
                    summary[c + ("groups",)] += 1
    table = []
    for level, ticker, stype in sorted({k[:3] for k in summary}):
        get = lambda name: summary[(level, ticker, stype, name)]  # noqa: E731
        table.append({"level": level, "ticker": ticker, "section_type": stype, "units": get("units"),
                      "repeated_units": get("repeated_units"), "reusable_units": get("reusable_units"),
                      "groups": get("groups")})
    return list(rows.values()), table


def samples(units: list[dict], rows: list[dict], n: int = 40, seed: int = 0) -> list[dict]:
    """Units where two levels disagree, with the first occurrence they were compared with."""
    by_id = {u["unit_id"]: u for u in units}
    out, rng = [], random.Random(seed)
    for kind, loose, strict in (("same text, other context", "text", "context"),
                                ("same text and context, other section_path", "context", "path")):
        picked = [r for r in rows if r[f"{loose}_group"] and r[f"{loose}_first_unit_id"] != r["unit_id"]
                  and not r[f"{strict}_group"]]
        for r in rng.sample(picked, min(n, len(picked))):
            u, first = by_id[r["unit_id"]], by_id[r[f"{loose}_first_unit_id"]]
            out.append({"kind": kind, "ticker": u["ticker"], "text": u["text"][:200],
                        "accession": u["accession_number"], "section_path": u["section_path"],
                        "first_accession": first["accession_number"], "first_section_path": first["section_path"]})
    return out


def write_csv(path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # -sig: Excel reads it as UTF-8
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["empty"])
        w.writeheader()
        w.writerows(rows)


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    context_size = settings()["label"]["context_sentences"]
    filing_order = {f["accession"]: k for k, f in enumerate(filings())}
    rows, table = build(units, context_size, filing_order)
    out_dir().mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir() / "unit_repeats.jsonl", rows)
    write_csv(out_dir() / "summary.csv", table)
    write_csv(out_dir() / "samples.csv", samples(units, rows))
    log(STEP, f"{len(rows)} units step 5 would label (context: {context_size} units before)")
    for level in LEVELS:
        lv = [t for t in table if t["level"] == level]
        rep, reuse = sum(t["repeated_units"] for t in lv), sum(t["reusable_units"] for t in lv)
        log(STEP, f"  {level:<8} {rep:>7} repeated ({rep / max(len(rows), 1):.0%}), "
                  f"{reuse:>7} reusable ({reuse / max(len(rows), 1):.0%}), "
                  f"{len({r[level + '_group'] for r in rows if r[level + '_group']}):>6} groups")
    log(STEP, f"-> {out_dir()}")


if __name__ == "__main__":
    run()
