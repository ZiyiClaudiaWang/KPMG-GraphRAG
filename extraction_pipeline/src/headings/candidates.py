"""The sub-headings step 4 finds in each kept section, and the levels file it reads back.

The file data/interim/heading_levels.jsonl has one row per heading:
{"heading_id", "text", "level", "source", "reason"}. heading_id is
accession|section_key|start of the heading in the Data-team content (segment.heading_id), so a
level survives a change of the rules elsewhere. Level 1 is the top of a section; level 0 is not a
heading (a column header, a table of contents) and changes no path. A heading the file does not
list is read by step 4 as a sibling of the heading before it.
"""

from __future__ import annotations

from ..common import Paths, data_dir, filings, read_jsonl, settings
from ..filter_sections import kept_section_keys


def levels_path():
    return data_dir() / "interim" / "heading_levels.jsonl"


def load_levels() -> dict[str, int]:
    """heading id -> level; empty when step 4b has not run."""
    path = levels_path()
    if not path.exists():
        return {}
    return {r["heading_id"]: int(r["level"]) for r in read_jsonl(path)}


def section_headings() -> list[dict]:
    """Sub-headings of every kept section, in order, with the first words of the text under each."""
    from ..segment import heading_id, segment_section
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
                              "next_text": " ".join(after.split()[:12])})
            if heads:
                out.append({"ticker": f["ticker"], "form": f["form"], "accession": sec["accession_number"],
                            "section_key": sec["section_key"], "section": units[0]["section_label"],
                            "headings": heads})
    return out
