"""Step 7a - Risk factors: place every Item 1A sentence under its risk category and risk themes.

Input : data/output/sentences.jsonl, data/output/labels.jsonl (optional),
        config/risk_themes.csv       the risk theme library: risk_id, name, definition
        config/risk_theme_map.csv    risk category text -> risk_ids (reviewed by hand)
Output: data/output/risk_categories.csv     one row per risk category heading in each filing
        data/interim/risk_assignments.jsonl one row per Item 1A sentence

Three levels (README, risk factors):
  risk_category       a category heading the filing itself uses ("Business Risks"). Its id is
                      the sentence_id of that heading, so it is unique to one filing.
  risk_theme          an entry of the library, shared by all companies and filings (risk_id).
                      A category maps to zero, one or several themes through risk_theme_map.csv.
  risk_lead_sentence  the sentence that opens and sums up one risk (flagged by the LLM as
                      is_risk_heading; without an LLM label, the step 4 rule proposal).

Every sentence belongs to the category heading above it, by position, and takes that
category's themes. Sentences before the first category heading have none. Each sentence
also records the risk lead sentence above it within the same category; this link is kept
for later and does not reach the graph yet.

A category text missing from risk_theme_map.csv is listed as "unmapped" and gets no theme;
add it to the map to connect it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .common import CONFIG_DIR, Paths, log, read_csv, read_jsonl, write_csv, write_jsonl

STEP = "risk_factors"

CATEGORY_FIELDS = ["risk_category_id", "accession_number", "ticker", "form", "section_key",
                   "category_text", "risk_summary", "n_units", "risk_ids", "map_status"]


def themes_path() -> Path:
    return Path(os.environ.get("PIPELINE_RISK_THEMES") or CONFIG_DIR / "risk_themes.csv")


def theme_map_path() -> Path:
    """config/risk_theme_map.csv, or the file named by PIPELINE_RISK_THEME_MAP (used by tests)."""
    return Path(os.environ.get("PIPELINE_RISK_THEME_MAP") or CONFIG_DIR / "risk_theme_map.csv")


def category_key(text: str) -> str:
    """Case, curly quotes and spacing do not matter when matching a category to the map."""
    return " ".join(text.lower().replace("’", "'").rstrip(".:").split())


def theme_library() -> dict[str, dict]:
    return {row["risk_id"]: row for row in read_csv(themes_path())}


def theme_map(library: dict[str, dict]) -> dict[str, list[str]]:
    """category key -> risk_ids; an empty list marks a heading that is not a theme."""
    mapping = {}
    for row in read_csv(theme_map_path()):
        ids = [x.strip() for x in (row["risk_ids"] or "").split(";") if x.strip()]
        unknown = [x for x in ids if x not in library]
        if unknown:
            raise ValueError(f"{theme_map_path().name}: {row['category_text']!r} uses unknown risk_id {unknown}")
        mapping[category_key(row["category_text"])] = ids
    return mapping


def is_lead_sentence(unit: dict, labels: dict[str, dict]) -> bool:
    label = labels.get(unit["sentence_id"])
    if label and label["source"] in ("llm", "llm_repair"):
        return bool(label["is_risk_heading"])
    return bool(unit.get("rule_heading"))


def assign(units: list[dict], labels: dict[str, dict],
           mapping: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    """Return one row per category heading and one assignment per Item 1A unit."""
    categories, assignments = [], []
    section = category = lead = None
    for u in units:
        if u["section_type"] != "risk_factors":
            continue
        if (u["accession_number"], u["section_key"]) != section:
            section, category, lead = (u["accession_number"], u["section_key"]), None, None
        if u["role"] == "category_heading":
            key = category_key(u["text"])
            ids = mapping.get(key)
            category = {
                "risk_category_id": u["sentence_id"], "accession_number": u["accession_number"],
                "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                "category_text": u["text"], "risk_summary": u.get("risk_summary", False), "n_units": 0,
                "risk_ids": ids or [],
                "map_status": "unmapped" if ids is None else "mapped" if ids else "not_a_theme",
            }
            categories.append(category)
            lead = None
            continue
        lead_here = is_lead_sentence(u, labels)
        if lead_here:
            lead = u["sentence_id"]
        if category:
            category["n_units"] += 1
        assignments.append({
            "sentence_id": u["sentence_id"],
            "risk_category_id": category["risk_category_id"] if category else None,
            "risk_ids": list(category["risk_ids"]) if category else [],
            "risk_lead_sentence": lead_here,
            "risk_lead_sentence_id": lead,
        })
    return categories, assignments


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    labels = {l["sentence_id"]: l for l in read_jsonl(Paths.labels())} if Paths.labels().exists() else {}
    library = theme_library()
    categories, assignments = assign(units, labels, theme_map(library))

    write_csv(Paths.risk_categories(), [dict(c, risk_ids=";".join(c["risk_ids"])) for c in categories],
              CATEGORY_FIELDS)
    write_jsonl(Paths.risk_assignments(), assignments)
    status = {s: sum(c["map_status"] == s for c in categories) for s in ("mapped", "not_a_theme", "unmapped")}
    with_theme = sum(bool(a["risk_ids"]) for a in assignments)
    log(STEP, f"{len(categories)} risk categories ({status}) -> {Paths.risk_categories()}")
    log(STEP, f"{with_theme} of {len(assignments)} Item 1A units have a risk theme -> {Paths.risk_assignments()}")
    for c in categories:
        if c["map_status"] == "unmapped":
            log(STEP, f"unmapped category, add it to {theme_map_path().name}: {c['ticker']} {c['category_text']!r}")


if __name__ == "__main__":
    run()
