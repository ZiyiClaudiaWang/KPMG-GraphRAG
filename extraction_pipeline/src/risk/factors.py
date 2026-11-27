"""Step 5 - Risk factors: the risk factors and risk categories of every Item 1A unit (no LLM).

Input : data/output/sentences.jsonl (step 4),
        config/risk_taxonomy/risk_category_map.csv   heading text -> groups (reviewed by hand)
Output: data/output/risk_categories.csv      one row per risk category heading in each filing
        data/interim/risk_factors.jsonl      one row per risk factor (its units), read by step 5b
        data/interim/risk_assignments.jsonl  one row per Item 1A unit, read by steps 5b and 9
        data/checks/risk_factors/category_checks.csv   are the categories complete?

Names (every id here is a unit_id):
  risk factor    one SEC risk: a lead sentence (step 4 lead_source) and the units after it, up to
                 the next lead or heading at its level, sub-headings below the lead included
                 (risk_factors(); wider than step 4's lead_unit_id). risk_factor_id = the lead's
                 unit_id. Step 5b gives each risk factor its themes.
  risk category  the filing's own level-1 heading in Item 1A ("Business Risks").
                 risk_category_unit_id = that heading's unit_id.

heading_risk_group: the groups the category heading names, from risk_category_map.csv. Every
unit takes the category above it, by position. Empty for a broad heading ("General Risks"),
outside a category, or an unmapped text: add a text listed as "unmapped" to the map.
no_group_reasons() says why units have no group; the review report gives it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from . import taxonomy as risk_taxonomy
from ..common import Paths, checks_dir, filings, log, read_jsonl, write_csv, write_jsonl
from .taxonomy import category_key
from ..segment import is_category_unit
from ..segment.headings import RISK_CATEGORY_START
from ..segment.risk import RISK_SUMMARY

STEP = "risk_factors"
UNIT_ROLES = ("body", "list_group")          # besides headings, the roles a risk factor covers
# Members of a risk factor besides its lead: units step 7 labels (no prefilter), list intros and
# sub-headings (decided 26 Nov: content). Other not_a_claim units (table_residue, definition,
# cross_reference, too_short) stay out: they never become Claims, and leaving them out keeps a
# saved step 5b answer valid when only they change.
MEMBER_PREFILTERS = (None, "list_intro", "heading")

CATEGORY_FIELDS = ["risk_category_unit_id", "accession_number", "ticker", "form", "section_key",
                   "category_text", "risk_summary", "n_units", "heading_risk_group", "map_status"]


# ---------- risk factors ----------

def risk_factors(units: list[dict]) -> list[dict]:
    """One entry per Item 1A lead outside the risk factor summary, with the units it covers, in order.

    A risk factor runs from its lead to the next lead, the next heading at its own level or above,
    or the end of the section. Its own level is that of the heading above the lead (at least 1,
    so a category heading always ends it); a lead that follows another open risk factor is its
    sibling and takes the same level. So a sub-heading below the lead, such as Microsoft's italic
    sub-topics ("Competition in the technology sector") under a bold lead, stays inside the risk
    factor, and so do the sentences under it.

    This is wider than step 4's lead_unit_id, which stops at any heading (kept as decided on
    26 Nov: the sentences under a sub-topic need no link to the lead). risk_factor_id is only
    the risk factor's id, used for its themes.

    units: the entry's units, the lead first (unit_ids of every unit that takes its themes).
    """
    out, current, section, heading_level = [], None, None, 0
    for u in units:
        if u["section_type"] != "risk_factors":
            continue
        if (u["accession_number"], u["section_key"]) != section:
            section, current, heading_level = (u["accession_number"], u["section_key"]), None, 0
        if u.get("risk_summary") or u["role"] == "section_title":
            current = None
            continue
        if u["role"] == "heading":
            level = u.get("heading_level")
            if isinstance(level, int) and level > 0:
                if current is not None and level <= current["level"]:
                    current = None
                heading_level = level
            if current is not None and u.get("prefilter") in MEMBER_PREFILTERS:
                current["units"].append(u)
            continue
        if u["role"] not in UNIT_ROLES:
            continue
        if u.get("lead_source"):
            level = current["level"] if current is not None else max(heading_level, 1)
            current = {"risk_factor_id": u["unit_id"], "accession_number": u["accession_number"],
                       "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                       "lead_text": u["text"], "level": level, "units": [u]}
            out.append(current)
        elif current is not None and u.get("prefilter") in MEMBER_PREFILTERS:
            current["units"].append(u)
    return out


def risk_factor_of(units: list[dict]) -> dict[str, str]:
    """unit_id -> risk_factor_id, for the lead itself and every unit under it."""
    return {u["unit_id"]: rf["risk_factor_id"] for rf in risk_factors(units) for u in rf["units"]}


# ---------- risk categories ----------

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
            "risk_summary": bool(u.get("risk_summary")),
            "prefilter": u.get("prefilter"),
        })
    of = risk_factor_of(units)
    for a in assignments:
        a["risk_factor_id"] = of.get(a["unit_id"])
    return categories, assignments


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


def factor_rows(factors: list[dict]) -> list[dict]:
    """risk_factors.jsonl: one row per risk factor, its units by unit_id (the lead first)."""
    return [{k: rf[k] for k in ("risk_factor_id", "accession_number", "ticker", "form", "section_key",
                                "lead_text", "level")} | {"unit_ids": [u["unit_id"] for u in rf["units"]]}
            for rf in factors]


def load_factors() -> list[dict]:
    """Step 5's risk factors (risk_factors.jsonl); empty when the step has not run."""
    path = Paths.risk_factors()
    return list(read_jsonl(path)) if path.exists() else []


def build(units: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """categories, assignments (heading fields and risk_factor_id) and risk factor rows, from step 4."""
    categories, assignments = assign(units, risk_taxonomy.category_map())
    return categories, assignments, factor_rows(risk_factors(units))


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    categories, assignments, factors = build(units)

    write_csv(Paths.risk_categories(), [dict(c, heading_risk_group=";".join(c["heading_risk_group"]))
                                        for c in categories], CATEGORY_FIELDS)
    write_jsonl(Paths.risk_factors(), factors)
    write_jsonl(Paths.risk_assignments(), assignments)
    checks = category_checks(units, categories)
    checks_path().parent.mkdir(parents=True, exist_ok=True)
    write_csv(checks_path(), checks, CHECK_FIELDS)
    log(STEP, f"category checks {dict(Counter(r['check'] for r in checks))} -> {checks_path()}")
    status = Counter(c["map_status"] for c in categories)
    with_group = sum(bool(a["heading_risk_group"]) for a in assignments)
    log(STEP, f"{len(categories)} risk categories ({dict(status)}); {with_group} of {len(assignments)} "
              f"Item 1A units have a heading group -> {Paths.risk_categories()}")
    in_factor = sum(bool(a["risk_factor_id"]) for a in assignments)
    members = sum(a["prefilter"] in MEMBER_PREFILTERS for a in assignments)
    log(STEP, f"{len(factors)} risk factors; {in_factor} of {members} Item 1A units that can be members "
              f"(of {len(assignments)}; other not_a_claim units are left out) are in one "
              f"-> {Paths.risk_factors()}, {Paths.risk_assignments()}")
    for c in categories:
        if c["map_status"] == "unmapped":
            log(STEP, f"unmapped category, add it to {risk_taxonomy.category_map_path().name}: "
                      f"{c['ticker']} {c['category_text']!r}")


if __name__ == "__main__":
    run()
