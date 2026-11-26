"""The headings step 4 finds in each kept section (sub-headings, and risk category headings in
Item 1A), and the levels file it reads back.

The file data/interim/heading_levels.jsonl has one row per heading:
{"heading_id", "text", "level", "source", "reason"}. heading_id is
accession|section_key|start of the heading in the Data-team content (segment.heading_id), so a
level survives a change of the rules elsewhere. Level 1 is the top of a section; level 0 is not a
heading (a column header, a table of contents) and changes no path. A heading the file does not
list is read by step 4 as a sibling of the heading before it.

data/interim/lead_sentences.jsonl (leads.py) has one row per filing:
{"accession", "html", "leads": [{"section_key", "start", "end", "source", "text"}]}, positions in
the section's Data-team content. Step 4 reads it for lead_source and lead_unit_id.
"""

from __future__ import annotations

from ..common import Paths, data_dir, filings, read_jsonl, settings
from ..filter_sections import kept_section_keys

# Levels in heading_levels.jsonl that are not a depth (step 4 reads them in segment/headings.py).
NOT_A_HEADING = 0      # a column header, "(In millions)", a table of contents: changes no path
SENTENCE_LEVEL = -1    # a sentence of the text, not a heading (a lead sentence styled as a heading)


def levels_path():
    return data_dir() / "interim" / "heading_levels.jsonl"


def leads_path():
    return data_dir() / "interim" / "lead_sentences.jsonl"


def load_leads() -> dict[str, dict]:
    """accession -> {"html": bool, "leads": {section_key: [{start, end, source, text}, ...]},
    "runin_headings": {section_key: [{start, end, text}, ...]}}; empty when step 4b has not run.
    A filing it does not list counts as one without HTML. A file from before run-in headings
    has none."""
    path = leads_path()
    if not path.exists():
        return {}
    out = {}
    for r in read_jsonl(path):
        by_section: dict[str, list[dict]] = {}
        for x in r["leads"]:
            by_section.setdefault(x["section_key"], []).append(x)
        runins: dict[str, list[dict]] = {}
        for x in r.get("runin_headings", []):
            runins.setdefault(x["section_key"], []).append(x)
        out[r["accession"]] = {"html": bool(r["html"]), "leads": by_section, "runin_headings": runins}
    return out


def tables_path():
    return data_dir() / "interim" / "html_tables.jsonl"


def load_tables() -> dict[str, dict]:
    """accession -> {"html": bool, "rows": {section_key: [{start, end, kind, table}, ...]}};
    empty when step 4b has not run. A filing it does not list counts as one without HTML
    (step 4 then keeps its text rule for tables)."""
    path = tables_path()
    if not path.exists():
        return {}
    out = {}
    for r in read_jsonl(path):
        by_section: dict[str, list[dict]] = {}
        for x in r["rows"]:
            by_section.setdefault(x["section_key"], []).append(x)
        out[r["accession"]] = {"html": bool(r["html"]), "rows": by_section}
    return out


# 4b reasons that only repeat the level of the heading before (levels.py): not evidence of depth.
SIBLING_REASONS = frozenset({"inside_paragraph", "plain_style", "unmatched", "missing_html"})


def load_levels() -> dict[str, int]:
    """heading id -> level; empty when step 4b has not run."""
    path = levels_path()
    if not path.exists():
        return {}
    return {r["heading_id"]: int(r["level"]) for r in read_jsonl(path)}


def load_guessed_levels() -> set[str]:
    """heading ids whose level 4b only guessed from the heading before (SIBLING_REASONS)."""
    path = levels_path()
    if not path.exists():
        return set()
    return {r["heading_id"] for r in read_jsonl(path) if r.get("reason") in SIBLING_REASONS}


def section_headings() -> list[dict]:
    """Headings of every kept section, in order, with the first words of the text under each.
    Item 1A headings are included: their level decides which of them are risk categories
    (level 1, segment.Levels.risk_heading_level). Each section also carries its units in short form (role,
    prefilter, rule_heading, start / end and spans in the Data-team content) for the HTML comparison
    (compare.py); a section without headings is listed too, with an empty list."""
    from ..segment import heading_id, is_category_unit, segment_section
    cfg = settings()["segment"]
    kept = kept_section_keys()
    out = []
    for f in filings():
        path = Paths.cleaned(f)
        if not path.exists():
            continue
        for sec in read_jsonl(path):
            if (sec["accession_number"], sec["section_key"]) not in kept:
                continue
            units = segment_section(sec, cfg)
            heads = []
            for k, u in enumerate(units):
                if u["role"] != "heading":
                    continue
                after = next((v["text"] for v in units[k + 1:] if v["role"] != "heading"), "")
                para = sec["paragraphs"][u["paragraph_index"]]
                heads.append({"heading_id": heading_id(sec, para, u["char_start"]), "text": u["text"],
                              "role": u["role"], "category_by_look": is_category_unit(u),
                              "next_text": " ".join(after.split()[:12])})
            if units:
                out.append({"ticker": f["ticker"], "form": f["form"], "accession": sec["accession_number"],
                            "section_key": sec["section_key"], "section": units[0]["section_label"],
                            "headings": heads, "units": [short_unit(u) for u in units]})
    return out


def short_unit(unit: dict) -> dict:
    """start / end cover the whole unit; spans are its pieces. A unit joined across a page break
    has two or more spans, and the page header or footer that step 2 removed between them lies
    inside start / end but in no span."""
    spans = unit["raw_spans"] or [[unit["char_start"], unit["char_end"]]]
    return {"role": unit["role"], "prefilter": unit["prefilter"], "rule_heading": unit["rule_heading"],
            "start": min(a for a, _ in spans), "end": max(b for _, b in spans),
            "spans": [list(sp) for sp in spans]}
