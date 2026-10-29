"""Step 3 - Filter: decide which sections to extract Claims from.

Input : data/interim/clean/*.jsonl, config/sections.yaml
Output: data/output/section_manifest.csv   (one row per section; review it before step 4)

decision is keep, drop or review. review means "not covered by the rules":
it is dropped until you add an override in config/sections.yaml.
The text statistics help with that decision; they never change it.
"""

from __future__ import annotations

import re

from .common import Paths, filings, load_config, log, read_csv, read_jsonl, write_csv

STEP = "filter"

FIELDS = [
    "accession_number", "ticker", "form", "section_order", "section_key", "section_type",
    "original_title", "n_chars", "n_paragraphs", "table_share", "digit_share",
    "decision", "reason",
]


def text_stats(section: dict) -> dict:
    """Share of characters in table rows and share of digits, to spot data-heavy sections."""
    text = section["text"]
    n = max(len(text), 1)
    table_chars = sum(len(p["text"]) for p in section["paragraphs"] if p["text"].count("|") >= 2)
    digits = len(re.findall(r"\d", text))
    return {
        "n_chars": len(text),
        "n_paragraphs": len(section["paragraphs"]),
        "table_share": round(table_chars / n, 2),
        "digit_share": round(digits / n, 3),
    }


def decide(section: dict, rules: dict) -> tuple[str, str]:
    key = f"{section['accession_number']}|{section['section_key']}"
    override = (rules.get("overrides") or {}).get(key)
    if override:
        return override, "manual override"
    stype = section["section_type"]
    if stype in rules["keep"]:
        return "keep", rules["keep"][stype]
    if stype in rules["drop"]:
        return "drop", rules["drop"][stype]
    if stype in (rules.get("review") or {}):
        return "review", rules["review"][stype]
    return "review", f"section_type '{stype}' is not in config/sections.yaml"


def run() -> None:
    rules = load_config("sections")
    rows = []
    for f in filings():
        for sec in read_jsonl(Paths.cleaned(f)):
            decision, reason = decide(sec, rules)
            rows.append({**{k: sec.get(k) for k in FIELDS}, **text_stats(sec),
                         "decision": decision, "reason": reason})
    write_csv(Paths.manifest(), rows, FIELDS)
    counts = {d: sum(r["decision"] == d for r in rows) for d in ("keep", "drop", "review")}
    log(STEP, f"{len(rows)} sections: {counts} -> {Paths.manifest()}")
    for r in rows:
        if r["decision"] == "review":
            log(STEP, f"  review: {r['ticker']} {r['form']} {r['section_key']} ({r['n_chars']} chars)")


def kept_section_keys() -> set[tuple[str, str]]:
    """(accession_number, section_key) pairs marked keep in the manifest."""
    return {(r["accession_number"], r["section_key"])
            for r in read_csv(Paths.manifest()) if r["decision"] == "keep"}


if __name__ == "__main__":
    run()
