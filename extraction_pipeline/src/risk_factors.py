"""Step 7a - Risk factors: the filing's risk category and the risk themes of every Item 1A unit.

Input : data/output/sentences.jsonl (step 4),
        config/risk_taxonomy/risk_category_map.csv   heading text -> groups (reviewed by hand)
        config/risk_taxonomy/themes.csv              the 32 themes and their groups
        data/interim/risk_themes.jsonl               step 4c, the classifier (optional)
Output: data/output/risk_categories.csv      one row per risk category heading in each filing
        data/interim/risk_assignments.jsonl  one row per Item 1A unit, read by step 7b
        data/checks/risk_factors/category_checks.csv     are the categories complete?
        data/checks/risk_factors/risk_factor_themes.csv  one row per risk factor, for review

Names: a "risk factor" is one SEC risk, a lead sentence and the units under it (step 4
lead_unit_id); its id, risk_factor_id, is the lead's unit_id. A "risk category" is the filing's
own level-1 heading in Item 1A ("Business Risks"); its id, risk_category_unit_id, is that
heading's unit_id. Every id here is a unit_id.

Two sources, kept apart:
  heading_risk_group  the groups the category heading names, from risk_category_map.csv (no
                      LLM). Every unit takes the category above it, by position. Empty for a
                      broad heading ("General Risks"), outside a category, or an unmapped text.
  risk_themes         from step 4c: the risk factor's primary theme on every unit of it
                      (scope risk_factor), a secondary theme only on the units the model gave
                      for it (scope sentence). On the primary, agrees_with_heading says whether
                      the theme's group is one the heading names (null when it names none).
risk_theme_status: classified | unknown (the model found no theme) | not_classified (step 4c
has no answer for this risk factor, or one from before its units changed) | no_risk_factor
(an Item 1A unit under no lead) | risk_summary; null outside Item 1A.

A category text missing from risk_category_map.csv is listed as "unmapped" and gets no group;
add it to the map. no_group_reasons() says why units have no group; the review report gives it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from . import risk_taxonomy, risk_themes
from .common import Paths, checks_dir, filings, log, read_jsonl, write_csv, write_jsonl
from .risk_taxonomy import category_key
from .segment import is_category_unit
from .segment.headings import RISK_CATEGORY_START
from .segment.risk import RISK_SUMMARY

STEP = "risk_factors"
THEME_SOURCE = "llm"           # HAS_RISK_THEME.source: themes come from the classifier (step 4c)

CATEGORY_FIELDS = ["risk_category_unit_id", "accession_number", "ticker", "form", "section_key",
                   "category_text", "risk_summary", "n_units", "heading_risk_group", "map_status"]
THEME_STATUSES = ["classified", "unknown", "not_classified", "no_risk_factor", "risk_summary"]
FACTOR_FIELDS = ["risk_factor_id", "ticker", "form", "accession_number", "category_text", "heading_risk_group",
                 "lead_text", "n_units", "primary_theme", "primary_group", "secondary_themes",
                 "agrees_with_heading", "reference_theme", "review"]


def assign(units: list[dict], mapping: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Return one row per category heading and one assignment per Item 1A unit (heading fields only)."""
    categories, assignments = [], []
    section = category = None
    for u in units:
        if u["section_type"] != "risk_factors":
            continue
        if (u["accession_number"], u["section_key"]) != section:
            section, category = (u["accession_number"], u["section_key"]), None
        if is_category_unit(u):
            hit = mapping.get(category_key(u["text"]))
            category = {
                "risk_category_unit_id": u["unit_id"], "accession_number": u["accession_number"],
                "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                "category_text": u["text"], "risk_summary": u.get("risk_summary", False), "n_units": 0,
                "heading_risk_group": list(hit["groups"]) if hit else [],
                "map_status": hit["status"] if hit else "unmapped",
            }
            categories.append(category)
            continue
        if category:
            category["n_units"] += 1
        assignments.append({
            "unit_id": u["unit_id"],
            "risk_category_unit_id": category["risk_category_unit_id"] if category else None,
            "risk_category_text": category["category_text"] if category else None,
            "heading_risk_group": list(category["heading_risk_group"]) if category else [],
        })
    return categories, assignments


def add_themes(units: list[dict], assignments: list[dict], results: dict[str, dict]) -> list[dict]:
    """Put risk_factor_id, risk_theme_status and risk_themes on each assignment; return one row per risk factor.

    results: step 4c rows by risk_factor_id. A row whose unit_ids differ from the risk factor's
    units now (step 4 ran again since) is not used.
    """
    themes = risk_taxonomy.themes()
    factors = risk_themes.risk_factors(units)
    of = {u["unit_id"]: rf for rf in factors for u in rf["units"]}
    by_id = {u["unit_id"]: u for u in units}
    answered = {}
    for rf in factors:
        r = results.get(rf["risk_factor_id"])
        current = r and r["unit_ids"] == [u["unit_id"] for u in rf["units"]]
        known = r and (r["primary_theme"] in themes or r["primary_theme"] == risk_taxonomy.UNKNOWN) \
            and all(x["theme_id"] in themes for x in r["secondary_themes"])     # not from an older taxonomy
        if current and known:
            answered[rf["risk_factor_id"]] = r
    for a in assignments:
        rf = of.get(a["unit_id"])
        a["risk_factor_id"] = rf["risk_factor_id"] if rf else None
        a["risk_themes"] = []
        if rf is None:
            a["risk_theme_status"] = "risk_summary" if by_id[a["unit_id"]].get("risk_summary") else "no_risk_factor"
            continue
        r = answered.get(rf["risk_factor_id"])
        if r is None:
            a["risk_theme_status"] = "not_classified"
            continue
        edge = {"source": THEME_SOURCE, "model": r["model"], "taxonomy_version": r["taxonomy_version"]}
        primary = r["primary_theme"]
        a["risk_theme_status"] = "unknown" if primary == risk_taxonomy.UNKNOWN else "classified"
        if primary != risk_taxonomy.UNKNOWN:
            heading = a["heading_risk_group"]
            agrees = (themes[primary]["group_id"] in heading) if heading else None
            a["risk_themes"].append({"theme_id": primary, "rank": "primary", "scope": "risk_factor",
                                     "agrees_with_heading": agrees, **edge})
        for s in r["secondary_themes"]:
            if a["unit_id"] in s["unit_ids"]:
                a["risk_themes"].append({"theme_id": s["theme_id"], "rank": "secondary", "scope": "sentence",
                                         "agrees_with_heading": None, **edge})
    by_unit = {a["unit_id"]: a for a in assignments}
    reference = risk_taxonomy.reference_labels()
    rows = []
    for rf in factors:
        r, lead = answered.get(rf["risk_factor_id"]), by_unit.get(rf["risk_factor_id"], {})
        primary = r["primary_theme"] if r else None
        heading = lead.get("heading_risk_group") or []
        agrees = (themes[primary]["group_id"] in heading) if heading and primary in themes else None
        ref = reference.get(category_key(rf["lead_text"]), "")
        review = ([] if r else ["not_classified"]) + (["unknown"] if primary == risk_taxonomy.UNKNOWN else []) \
            + (["differs_from_reference"] if r and ref and primary != ref else []) \
            + (["overrules_heading"] if agrees is False else [])
        rows.append({"risk_factor_id": rf["risk_factor_id"], "ticker": rf["ticker"], "form": rf["form"],
                     "accession_number": rf["accession_number"], "category_text": lead.get("risk_category_text"),
                     "heading_risk_group": ";".join(heading), "lead_text": rf["lead_text"],
                     "n_units": len(rf["units"]), "primary_theme": primary,
                     "primary_group": themes[primary]["group_id"] if primary in themes else None,
                     "secondary_themes": ";".join(f"{s['theme_id']}({len(s['unit_ids'])})"
                                                  for s in (r["secondary_themes"] if r else [])),
                     "agrees_with_heading": agrees, "reference_theme": ref, "review": ";".join(review)})
    return rows


# ---------- review: are the categories complete? ----------

CATEGORY_CHECKS = [
    ("year_gap", "A category this company's 10-Ks have the year before and the year after, missing here: a "
                 "level-1 heading read as a lower level or not found? (10-Qs left out: their Item 1A lists "
                 "only the risks that changed)"),
    ("summary_mismatch", "A category the risk factor summary lists with no level-1 heading of that text in the "
                         "full text: missed, or worded differently in the summary?"),
    ("risk_heading_below_1", "An Item 1A heading below level 1 that names a group of risks ('Risks related to "
                             "...', '... Risks'): a category read as a sub-heading?"),
    ("suspect_category", "A level-1 heading with fewer than 3 units under it, or without the word 'risk' "
                         "(texts the map marks as not_a_category left out): not a category?"),
]
CHECK_FIELDS = ["check", "ticker", "form", "reporting_period_end", "accession_number", "section_key",
                "text", "heading_level", "detail", "unit_id"]
GROUP_OF_RISKS = re.compile(r"\brisks?(?:\s+factors?)?\s*[.:]?$", re.IGNORECASE)
GROUP_MAX_WORDS = 15
SUSPECT_MIN_UNITS = 3
NO_GROUP_REASONS = ["risk_summary", "section_without_category", "before_first_category", "broad",
                    "not_a_category", "unmapped"]


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
        if not c["risk_summary"] and c["form"] == "10-K":
            keys[c["accession_number"]].add(category_key(c["category_text"]))
    series: dict[tuple, list[str]] = defaultdict(list)
    for c in categories:
        acc = c["accession_number"]
        if c["form"] != "10-K":
            continue
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
        if c["risk_summary"] or c["map_status"] == "not_a_category":
            continue
        why = []
        if c["n_units"] < SUSPECT_MIN_UNITS:
            why.append(f"{c['n_units']} units")
        if not re.search(r"\brisk", c["category_text"], re.IGNORECASE):
            why.append("no 'risk'")
        if why:
            rows.append(_row("suspect_category", by_id[c["risk_category_unit_id"]], periods, "; ".join(why)))
    return rows


def no_group_reasons(units: list[dict], categories: list[dict], assignments: list[dict]) -> Counter:
    """Why Item 1A units have no heading_risk_group (NO_GROUP_REASONS), counted over the units with none."""
    by_id = {u["unit_id"]: u for u in units}
    status = {c["risk_category_unit_id"]: c["map_status"] for c in categories}
    with_category = {(c["accession_number"], c["section_key"]) for c in categories}
    out = Counter()
    for a in assignments:
        if a["heading_risk_group"]:
            continue
        u = by_id[a["unit_id"]]
        if u.get("risk_summary"):
            out["risk_summary"] += 1
        elif a["risk_category_unit_id"] is None:
            in_categorised = (u["accession_number"], u["section_key"]) in with_category
            out["before_first_category" if in_categorised else "section_without_category"] += 1
        else:
            out[status[a["risk_category_unit_id"]]] += 1
    return out


def checks_path() -> Path:
    return checks_dir(STEP) / "category_checks.csv"


def factors_path() -> Path:
    return checks_dir(STEP) / "risk_factor_themes.csv"


def build(units: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """categories, assignments (with themes) and one row per risk factor, from step 4 and step 4c."""
    categories, assignments = assign(units, risk_taxonomy.category_map())
    factors = add_themes(units, assignments, risk_themes.load_results())
    return categories, assignments, factors


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    categories, assignments, factors = build(units)

    write_csv(Paths.risk_categories(), [dict(c, heading_risk_group=";".join(c["heading_risk_group"]))
                                        for c in categories], CATEGORY_FIELDS)
    write_jsonl(Paths.risk_assignments(), assignments)
    checks = category_checks(units, categories)
    checks_path().parent.mkdir(parents=True, exist_ok=True)
    write_csv(checks_path(), checks, CHECK_FIELDS)
    write_csv(factors_path(), factors, FACTOR_FIELDS)
    log(STEP, f"category checks {dict(Counter(r['check'] for r in checks))} -> {checks_path()}")
    status = Counter(c["map_status"] for c in categories)
    with_group = sum(bool(a["heading_risk_group"]) for a in assignments)
    log(STEP, f"{len(categories)} risk categories ({dict(status)}); {with_group} of {len(assignments)} "
              f"Item 1A units have a heading group -> {Paths.risk_categories()}")
    themed = Counter(a["risk_theme_status"] for a in assignments)
    review = Counter(x for f in factors for x in f["review"].split(";") if x)
    log(STEP, f"{len(factors)} risk factors; units by theme status {dict(themed)} -> {Paths.risk_assignments()}")
    log(STEP, f"risk factors to review {dict(review)} -> {factors_path()}")
    for c in categories:
        if c["map_status"] == "unmapped":
            log(STEP, f"unmapped category, add it to {risk_taxonomy.category_map_path().name}: "
                      f"{c['ticker']} {c['category_text']!r}")


if __name__ == "__main__":
    run()
