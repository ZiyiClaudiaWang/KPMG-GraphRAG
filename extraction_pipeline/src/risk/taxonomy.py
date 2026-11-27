"""The risk taxonomy in config/risk_taxonomy/: groups, themes and the heading -> group map.

  groups.csv             the 7 coarse groups (RiskGroup nodes)
  themes.csv             the 32 fine themes (RiskTheme nodes); the definitions are what the
                         classifier (step risk_themes) is given
  risk_category_map.csv  an Item 1A level-1 heading text (the filing's own risk category) ->
                         zero, one or several groups, reviewed by hand. status:
                           group           the heading names the groups in group_ids
                           broad           a real category that names no area ("Business Risks",
                                           "General Risks"): no agrees / overrules comparison
                           not_a_category  a level-1 heading that is no category (an index table,
                                           a repeated section title, the risk factor summary)
  reference_labels.csv   a draft primary theme per lead text, read from the title only; the
                         review compares the classifier with it (never shown to the model)

The model picks themes only; a theme's group follows from themes.csv.
PIPELINE_RISK_CATEGORY_MAP names another risk_category_map.csv (used by tests).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..common import CONFIG_DIR, read_csv

MAP_STATUSES = ("group", "broad", "not_a_category")
UNKNOWN = "unknown"           # the model's answer when no theme fits: a Claim property, not a node


def folder() -> Path:
    return CONFIG_DIR / "risk_taxonomy"


def category_map_path() -> Path:
    return Path(os.environ.get("PIPELINE_RISK_CATEGORY_MAP") or folder() / "risk_category_map.csv")


def category_key(text: str) -> str:
    """Case, curly quotes and spacing do not matter when matching a heading to the map."""
    return " ".join(text.lower().replace("’", "'").rstrip(".:").split())


def groups() -> dict[str, dict]:
    return {row["group_id"]: row for row in read_csv(folder() / "groups.csv")}


def themes() -> dict[str, dict]:
    """theme_id -> row (group_id, definition, ey_2016_category); every group_id must be in groups.csv."""
    known = groups()
    rows = {row["theme_id"]: row for row in read_csv(folder() / "themes.csv")}
    bad = {t: r["group_id"] for t, r in rows.items() if r["group_id"] not in known}
    if bad:
        raise ValueError(f"themes.csv: unknown group_id {bad}")
    return rows


def version() -> str:
    """Short hash of themes.csv: recorded on every theme edge, so a changed definition shows."""
    return hashlib.sha256((folder() / "themes.csv").read_bytes()).hexdigest()[:12]


def category_map() -> dict[str, dict]:
    """category key -> {"groups": [...], "status": ...}. Stops on an unknown group, status or a repeated text."""
    known = groups()
    out: dict[str, dict] = {}
    for row in read_csv(category_map_path()):
        key = category_key(row["category_text"])
        ids = [x.strip() for x in (row["group_ids"] or "").split(";") if x.strip()]
        status = (row.get("status") or "").strip()
        problem = (f"unknown group {[x for x in ids if x not in known]}" if any(x not in known for x in ids)
                   else f"status {status!r} is not one of {MAP_STATUSES}" if status not in MAP_STATUSES
                   else "status group needs group_ids" if status == "group" and not ids
                   else f"status {status} takes no group_ids" if status != "group" and ids
                   else "listed twice (case and spacing do not count)" if key in out else None)
        if problem:
            raise ValueError(f"{category_map_path().name}: {row['category_text']!r}: {problem}")
        out[key] = {"groups": ids, "status": status}
    return out


def reference_labels() -> dict[str, str]:
    """lead text (category_key form) -> draft primary theme; empty when the file is missing."""
    path = folder() / "reference_labels.csv"
    if not path.exists():
        return {}
    return {category_key(r["lead_text"]): r["theme_id"] for r in read_csv(path) if r.get("theme_id")}
