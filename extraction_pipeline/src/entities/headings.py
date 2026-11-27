"""Entities named in headings (step 6b): a subsidiary or segment in a heading covers the units under it.

Headings are not sent to the LLM (prefilter heading), and a model mention must be part of the
sentence itself, so "Sam's Club" in a heading above a paragraph was never linked. Step 6b runs
the dictionary pass of entities/annotation.py on every heading unit (code only, no LLM) and
writes data/interim/heading_entities.jsonl, one row per heading with a catalog hit:

  heading_unit_id, accession_number, section_key, text, heading_level, mentions (as in
  annotate_mentions: mention_text, entity_type, match_status, candidate_entity_ids, ...),
  related_segment_ids, related_subsidiary_ids (matched / matched_auto only)

Step 9 gives each Claim the linked entities of every heading in its heading_path (inherited()):
heading_entity_links [{entity_id, entity_type, mention_text, match_status, heading_unit_id,
heading_text}]. A heading text in heading_path is the latest heading unit with that text before
the Claim in its section. An entity the Claim's own sentence already links is left out (the
direct link is stronger). Step 10 loads them as MENTIONS edges with found_by ["heading"].
"""

from __future__ import annotations

from collections import defaultdict

from ..common import Paths, filings, log, read_jsonl, write_jsonl
from . import annotation

STEP = "entity_catalog"


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def heading_rows(units: list[dict], catalog: list[dict], cik: dict[str, str] | None = None) -> list[dict]:
    """Rows of heading_entities.jsonl: headings with at least one catalog hit. cik: accession ->
    the registrant's CIK (config/filings.yaml by default)."""
    cik = cik if cik is not None else {f["accession"]: f["cik"] for f in filings()}
    rows = []
    for u in units:
        if u["role"] != "heading" or u["accession_number"] not in cik:
            continue
        found = annotation.annotate_mentions(u["text"], [], issuer_cik=cik[u["accession_number"]],
                                             accession=u["accession_number"], catalog=catalog)
        mentions = [m for m in found["entity_mentions"]
                    if m.get("match_status") not in (None, "registrant_reference")]
        if not mentions:
            continue
        rows.append({"heading_unit_id": u["unit_id"], "accession_number": u["accession_number"],
                     "section_key": u["section_key"], "text": u["text"],
                     "heading_level": u.get("heading_level"), "mentions": mentions,
                     "related_segment_ids": found["related_segment_ids"],
                     "related_subsidiary_ids": found["related_subsidiary_ids"]})
    return rows


def inherited(units: list[dict], rows: list[dict]) -> dict[str, list[dict]]:
    """unit id -> linked entities of the headings in its heading_path (see the module docstring)."""
    by_heading = {r["heading_unit_id"]: r for r in rows}
    out: dict[str, list[dict]] = {}
    latest: dict[tuple, dict[str, str]] = defaultdict(dict)     # section -> heading text -> unit id
    for u in units:
        section = (u["accession_number"], u["section_key"])
        if u["role"] == "heading":
            latest[section][_norm(u["text"])] = u["unit_id"]
            continue
        links, seen = [], set()
        for name in u.get("heading_path") or []:
            hid = latest[section].get(_norm(name))
            row = by_heading.get(hid)
            if row is None:
                continue
            for kind, ids in (("segment", row["related_segment_ids"]),
                              ("subsidiary", row["related_subsidiary_ids"])):
                for eid in ids:
                    if eid in seen:
                        continue
                    seen.add(eid)
                    m = next((m for m in row["mentions"] if eid in m.get("candidate_entity_ids", [])), {})
                    links.append({"entity_id": eid, "entity_type": kind,
                                  "mention_text": m.get("mention_text"), "match_status": m.get("match_status"),
                                  "heading_unit_id": hid, "heading_text": row["text"]})
        if links:
            out[u["unit_id"]] = links
    return out


def run(catalog: list[dict]) -> None:
    if not Paths.sentences().exists():
        log(STEP, f"no {Paths.sentences()}: run step 4 first; no heading entities")
        write_jsonl(Paths.heading_entities(), [])
        return
    units = list(read_jsonl(Paths.sentences()))
    rows = heading_rows(units, catalog)
    write_jsonl(Paths.heading_entities(), rows)
    covered = inherited(units, rows)
    linked = sum(bool(r["related_segment_ids"] or r["related_subsidiary_ids"]) for r in rows)
    log(STEP, f"{len(rows)} headings name a catalog entity ({linked} linked); "
              f"{len(covered)} units under them -> {Paths.heading_entities()}")


def read_rows() -> list[dict]:
    return list(read_jsonl(Paths.heading_entities())) if Paths.heading_entities().exists() else []
