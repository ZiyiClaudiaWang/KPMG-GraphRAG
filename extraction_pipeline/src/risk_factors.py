"""Step 7a - Risk factors: place every Item 1A sentence under its risk category and risk themes.

Input : data/output/sentences.jsonl,
        config/risk_themes.csv       the risk theme library: risk_id, name, definition
        config/risk_theme_map.csv    risk category text -> risk_ids (reviewed by hand)
Output: data/output/risk_categories.csv     one row per risk category heading in each filing
        data/interim/risk_assignments.jsonl one row per Item 1A sentence

Two levels (README, risk factors):
  risk_category       a category heading the filing itself uses ("Business Risks"). Its id is
                      the unit_id of that heading (a position in the source, #45), so it is
                      unique to one filing and does not move when other sentences are re-split.
  risk_theme          an entry of the library, shared by all companies and filings (risk_id).
                      A category maps to zero, one or several themes through risk_theme_map.csv.

Every sentence belongs to the category heading above it, by position, and takes that
category's themes. Sentences before the first category heading have none. Every id here is
a unit_id. Lead sentences are not decided here: step 4 marks them in every section
(lead_source, lead_unit_id, from the filing's HTML) and step 7b reads them from there.

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


def assign(units: list[dict], mapping: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    """Return one row per category heading and one assignment per Item 1A unit."""
    categories, assignments = [], []
    section = category = None
    for u in units:
        if u["section_type"] != "risk_factors":
            continue
        if (u["accession_number"], u["section_key"]) != section:
            section, category = (u["accession_number"], u["section_key"]), None
        if u["role"] == "category_heading":
            key = category_key(u["text"])
            ids = mapping.get(key)
            category = {
                "risk_category_id": u["unit_id"], "accession_number": u["accession_number"],
                "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                "category_text": u["text"], "risk_summary": u.get("risk_summary", False), "n_units": 0,
                "risk_ids": ids or [],
                "map_status": "unmapped" if ids is None else "mapped" if ids else "not_a_theme",
            }
            categories.append(category)
            continue
        if category:
            category["n_units"] += 1
        assignments.append({
            "unit_id": u["unit_id"],
            "risk_category_id": category["risk_category_id"] if category else None,
            "risk_ids": list(category["risk_ids"]) if category else [],
        })
    return categories, assignments


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    library = theme_library()
    categories, assignments = assign(units, theme_map(library))

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
