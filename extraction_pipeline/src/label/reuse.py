"""Step 7 repeats: copy the label of a unit's first occurrence in an earlier filing (reuse_repeats)."""

from __future__ import annotations

from collections import defaultdict

from ..checks.unit_repeats import build as repeat_groups, norm
from ..common import filings


def reuse_plan(units: list[dict], cfg: dict) -> dict[str, str]:
    """unit id -> the first occurrence whose label it reuses (label.reuse_repeats), or {}."""
    level = cfg.get("reuse_repeats")
    if not level:
        return {}
    if level != "label":
        raise ValueError(f"label.reuse_repeats must be label or null, not {level!r}")
    order = {f["accession"]: k for k, f in enumerate(filings())}
    rows, _ = repeat_groups(units, cfg["context_sentences"], order)
    return {r["unit_id"]: r["label_first_unit_id"] for r in rows
            if r["label_first_unit_id"] not in (None, r["unit_id"])}


def units_before(units: list[dict]) -> dict[str, tuple[list[dict], int]]:
    """unit id -> (its section's units without section titles, how many of them come before it),
    as in checks.unit_repeats.unit_keys. seq[n - d] is the unit d places before it."""
    out, sections = {}, defaultdict(list)
    for u in units:
        seq = sections[(u["accession_number"], u["section_key"])]
        out[u["unit_id"]] = (seq, len(seq))
        if u["role"] != "section_title":
            seq.append(u)
    return out


def reused_labels(labels: list[dict], reuse: dict[str, str], units: list[dict]) -> tuple[list[dict], set[str]]:
    """Copies of first-occurrence labels for reused units, and the units that must be sent instead.

    A context unit the first occurrence cites is moved to the unit at the same distance before
    the reused unit, if that unit has the same text; otherwise the reused unit is sent. A unit
    whose first occurrence has no label yet gets neither (it waits for a later run).
    """
    by_unit = {l["unit_id"]: l for l in labels}
    before = units_before(units)
    index = {}                                           # unit id -> its place in its section's seq
    for seq in {id(seq): seq for seq, _ in before.values()}.values():
        index.update((u["unit_id"], k) for k, u in enumerate(seq))
    out, fallback = [], set()
    for uid, first_id in reuse.items():
        first = by_unit.get(first_id)
        if first is None:
            continue                                     # not labelled yet
        if first["source"] not in ("llm", "llm_repair", "llm_reused"):
            fallback.add(uid)                            # held by quarantine: no model label to copy
            continue
        (mine, n_mine), (theirs, n_theirs) = before[uid], before[first_id]
        moved = []
        for cid in first["context_unit_ids"]:
            k = index.get(cid)
            d = n_theirs - k if k is not None and k < n_theirs and theirs[k]["unit_id"] == cid else None
            if d is None or d > n_mine or norm(mine[n_mine - d]["text"]) != norm(theirs[n_theirs - d]["text"]):
                break
            moved.append(mine[n_mine - d]["unit_id"])
        else:
            out.append(dict(first, unit_id=uid, source="llm_reused", context_unit_ids=moved,
                            reused_from_unit_id=first_id))
            continue
        fallback.add(uid)
    return out, fallback
