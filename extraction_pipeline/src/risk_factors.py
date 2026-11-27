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

Review file (data/checks/risk_factors/category_checks.csv, one row per case): are the
categories complete? See CATEGORY_CHECKS. no_theme_reasons() says why Item 1A units have no
theme; the review report gives both.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .common import CONFIG_DIR, Paths, checks_dir, filings, log, read_csv, read_jsonl, write_csv, write_jsonl
from .segment import is_category_unit
from .segment.headings import RISK_CATEGORY_START
from .segment.risk import RISK_SUMMARY

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
        if is_category_unit(u):
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


# ---------- review: are the categories complete? ----------

CATEGORY_CHECKS = [
    ("year_gap", "A category this company's filings of the same form have just before and just after, "
                 "missing here: a level-1 heading read as a lower level or not found?"),
    ("summary_mismatch", "A category the risk factor summary lists with no level-1 heading of that text in the "
                         "full text: missed, or worded differently in the summary?"),
    ("risk_heading_below_1", "An Item 1A heading below level 1 that names a group of risks ('Risks related to "
                             "...', '... Risks'): a category read as a sub-heading?"),
    ("suspect_category", "A level-1 heading with fewer than 3 units under it, or without the word 'risk' "
                         "(texts the map marks as not a theme left out): not a category?"),
]
CHECK_FIELDS = ["check", "ticker", "form", "reporting_period_end", "accession_number", "section_key",
                "text", "heading_level", "detail", "unit_id"]
GROUP_OF_RISKS = re.compile(r"\brisks?(?:\s+factors?)?\s*[.:]?$", re.IGNORECASE)
GROUP_MAX_WORDS = 15
SUSPECT_MIN_UNITS = 3
NO_THEME_REASONS = ["risk_summary", "section_without_category", "before_first_category", "not_a_theme", "unmapped"]


def _row(check: str, u: dict, periods: dict, detail: str = "") -> dict:
    return {"check": check, "ticker": u["ticker"], "form": u["form"],
            "reporting_period_end": periods.get(u["accession_number"], ""),
            "accession_number": u["accession_number"], "section_key": u["section_key"], "text": u["text"],
            "heading_level": u.get("heading_level"), "detail": detail, "unit_id": u["unit_id"]}


def category_checks(units: list[dict], categories: list[dict]) -> list[dict]:
    """One row per case of CATEGORY_CHECKS. categories: from assign()."""
    order = {f["accession"]: i for i, f in enumerate(filings(include_on_hold=True))}
    periods = {f["accession"]: f.get("reporting_period_end", "") for f in filings(include_on_hold=True)}
    risk = [u for u in units if u["section_type"] == "risk_factors"]
    by_id = {u["unit_id"]: u for u in risk}
    rows = []

    # year_gap: neighbours in period order, among the filings of a company and form that have categories
    keys: dict[str, set] = defaultdict(set)
    for c in categories:
        if not c["risk_summary"]:
            keys[c["accession_number"]].add(category_key(c["category_text"]))
    series: dict[tuple, list[str]] = defaultdict(list)
    for c in categories:
        acc = c["accession_number"]
        if acc not in series[(c["ticker"], c["form"])]:
            series[(c["ticker"], c["form"])].append(acc)
    first_unit = {}
    for u in risk:
        first_unit.setdefault(u["accession_number"], u)
    for (ticker, form), accs in series.items():
        accs.sort(key=lambda a: order.get(a, len(order)))
        for prev, cur, nxt in zip(accs, accs[1:], accs[2:]):
            for key in sorted((keys[prev] & keys[nxt]) - keys[cur]):
                text = next(c["category_text"] for c in categories
                            if c["accession_number"] == prev and category_key(c["category_text"]) == key)
                rows.append(_row("year_gap", dict(first_unit[cur], text=text, unit_id="", heading_level=None),
                                 periods, f"in {prev} and {nxt}"))

    # summary_mismatch: headings inside the risk factor summary vs the full text's categories
    sections: dict[tuple, list[dict]] = defaultdict(list)
    for u in risk:
        sections[(u["accession_number"], u["section_key"])].append(u)
    for us in sections.values():
        full = {category_key(u["text"]) for u in us if is_category_unit(u) and not u.get("risk_summary")}
        for u in us:
            if (u.get("risk_summary") and u["role"] == "heading" and not RISK_SUMMARY.match(u["text"])
                    and u.get("heading_level", 1) != 0 and category_key(u["text"]) not in full):
                rows.append(_row("summary_mismatch", u, periods, "not a level-1 heading in the full text"))

    # risk_heading_below_1
    for u in risk:
        level = u.get("heading_level")
        if (u["role"] == "heading" and level is not None and level >= 2 and not u.get("risk_summary")
                and len(u["text"].split()) <= GROUP_MAX_WORDS
                and (RISK_CATEGORY_START.match(u["text"]) or GROUP_OF_RISKS.search(u["text"]))):
            rows.append(_row("risk_heading_below_1", u, periods, f"level {level}"))

    # suspect_category
    for c in categories:
        if c["risk_summary"] or c["map_status"] == "not_a_theme":
            continue
        why = []
        if c["n_units"] < SUSPECT_MIN_UNITS:
            why.append(f"{c['n_units']} units")
        if not re.search(r"\brisk", c["category_text"], re.IGNORECASE):
            why.append("no 'risk'")
        if why:
            rows.append(_row("suspect_category", by_id[c["risk_category_id"]], periods, "; ".join(why)))
    return rows


def no_theme_reasons(units: list[dict], categories: list[dict], assignments: list[dict]) -> Counter:
    """Why Item 1A units have no theme (NO_THEME_REASONS), counted over the units with none."""
    by_id = {u["unit_id"]: u for u in units}
    status = {c["risk_category_id"]: c["map_status"] for c in categories}
    with_category = {(c["accession_number"], c["section_key"]) for c in categories}
    out = Counter()
    for a in assignments:
        if a["risk_ids"]:
            continue
        u = by_id[a["unit_id"]]
        if u.get("risk_summary"):
            out["risk_summary"] += 1
        elif a["risk_category_id"] is None:
            in_categorised = (u["accession_number"], u["section_key"]) in with_category
            out["before_first_category" if in_categorised else "section_without_category"] += 1
        else:
            out[status[a["risk_category_id"]]] += 1
    return out


def checks_path() -> Path:
    return checks_dir(STEP) / "category_checks.csv"


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    library = theme_library()
    categories, assignments = assign(units, theme_map(library))

    write_csv(Paths.risk_categories(), [dict(c, risk_ids=";".join(c["risk_ids"])) for c in categories],
              CATEGORY_FIELDS)
    write_jsonl(Paths.risk_assignments(), assignments)
    checks = category_checks(units, categories)
    checks_path().parent.mkdir(parents=True, exist_ok=True)
    write_csv(checks_path(), checks, CHECK_FIELDS)
    log(STEP, f"category checks {dict(Counter(r['check'] for r in checks))} -> {checks_path()}")
    status = {s: sum(c["map_status"] == s for c in categories) for s in ("mapped", "not_a_theme", "unmapped")}
    with_theme = sum(bool(a["risk_ids"]) for a in assignments)
    log(STEP, f"{len(categories)} risk categories ({status}) -> {Paths.risk_categories()}")
    log(STEP, f"{with_theme} of {len(assignments)} Item 1A units have a risk theme -> {Paths.risk_assignments()}")
    for c in categories:
        if c["map_status"] == "unmapped":
            log(STEP, f"unmapped category, add it to {theme_map_path().name}: {c['ticker']} {c['category_text']!r}")


if __name__ == "__main__":
    run()
