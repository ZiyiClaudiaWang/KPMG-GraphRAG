"""Step 4b - Heading levels: how the sub-headings of a section nest.

Plain text shows that a line is a heading, not how deep it sits: in "Results of Operations" /
"Segment Results" / "Intelligent Cloud" each heading replaced the one before it, so
section_path kept only the last (README Remaining issues 1.3; 892 heading-after-heading pairs in
the kept sections of the 233 filings). Step 4 reads a level for each heading from
data/interim/heading_levels.jsonl and keeps the headings above a unit in section_path.

Where the levels come from:
  1. the Data team's HTML (tag, style or nesting of each heading), if they can export it: they
     write the same file, one row per heading, and this step is not run;
  2. otherwise this step: the model sees the list of sub-headings of a section, in order, and
     gives each a level (1 = top). Level 0 = not a heading (a table title or column header such
     as "Fiscal Year" or "(In millions)"), which then changes no path.

The file: one row per heading, {"heading_id", "level", "text", "source"}; heading_id is
accession|section_key|start of the heading in the Data-team content (segment.heading_id), so a
level survives a change of the rules elsewhere. A heading the file does not list is read as a
sibling of the heading before it.

Usage:
  python -m src.heading_levels --dry-run    sections, headings and estimated cost; no API calls
  python -m src.heading_levels              ask, then rerun step 4 (python run_pipeline.py --from segment)
  python -m src.heading_levels --limit 3    send at most 3 new requests

Money rules are label.py's (saved replies in data/heading_levels/<hash>/, no automatic resend,
budget_usd).
"""

from __future__ import annotations

import argparse
import re

from .common import Paths, data_dir, filings, log, read_jsonl, settings, write_jsonl
from .filter_sections import kept_section_keys

STEP = "heading_levels"
RUNNER_VERSION = "kelly_heading_levels_v1"
MAX_HEADINGS = 120             # headings per request; a longer section is sent in parts
MIN_HEADINGS = 2               # a section with fewer headings has nothing to nest

SYSTEM = """You restore the outline of a section of an SEC 10-K or 10-Q filing. You get its
sub-headings in order, as plain text, each with the first words of the text under it. Give each
heading a level: 1 for the top headings of the section, 2 for a heading under the nearest
level-1 heading above it, 3 under the nearest level 2, and so on (at most 4). A heading's level
can be at most one more than the level of the heading before it.
Give level 0 to lines that are not section headings: table titles, column or row headers
("Fiscal Year", "(In millions)", "Three Months Ended March 31,"), units, and footnote labels.
Use the wording and the typography the text still shows: capitals, title case, numbering
("Note 7", "(a)"), a trailing ":" or ".", and repeated patterns (the same kind of heading is
usually the same level). Return one result per heading id."""


def levels_path():
    return data_dir() / "interim" / "heading_levels.jsonl"


def load_levels() -> dict[str, int]:
    """heading id -> level, from this step or from the Data team; empty when neither exists."""
    path = levels_path()
    if not path.exists():
        return {}
    return {r["heading_id"]: int(r["level"]) for r in read_jsonl(path)}


def style(text: str) -> str:
    """The typography plain text still shows, as a hint for the model."""
    words = re.findall(r"[A-Za-z][A-Za-z’'-]*", text)
    if re.match(r"^(?:note\s+\d+|\d{1,2}\.\s|\([a-z0-9]\))", text, re.I):
        return "numbered"
    if words and text.upper() == text:
        return "capitals"
    if text.endswith(":"):
        return "ends with colon"
    if text.endswith("."):
        return "run-in, ends with period"
    long_words = [w for w in words if len(w) >= 4]
    if long_words and sum(w[0].isupper() for w in long_words) / len(long_words) >= 0.6:
        return "title case"
    return "sentence case"


def section_headings() -> list[dict]:
    """Sub-headings of every kept section, in order, with the start of the text under each."""
    from .segment import heading_id, segment_section
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
                              "style": style(u["text"]), "next_text": " ".join(after.split()[:12])})
            if len(heads) >= MIN_HEADINGS:
                out.append({"ticker": f["ticker"], "form": f["form"], "accession": sec["accession_number"],
                            "section_key": sec["section_key"], "section": u["section_label"], "headings": heads})
    return out


def schema() -> dict:
    row = {"type": "object", "additionalProperties": False, "required": ["id", "level"],
           "properties": {"id": {"type": "string"}, "level": {"type": "integer", "minimum": 0, "maximum": 4}}}
    return {"name": "heading_levels", "strict": True,
            "schema": {"type": "object", "additionalProperties": False, "required": ["results"],
                       "properties": {"results": {"type": "array", "items": row}}}}


def plans(sections: list[dict], cfg: dict) -> list[dict]:
    from .label import canonical, digest
    out = []
    for sec in sections:
        heads = sec["headings"]
        for part in range(0, len(heads), MAX_HEADINGS):
            chunk = heads[part:part + MAX_HEADINGS]
            payload = {"filing": f"{sec['ticker']} {sec['form']}", "section": sec["section"],
                       "headings": [{"id": f"h{k + 1}", "text": h["text"], "style": h["style"],
                                     "text_below": h["next_text"]} for k, h in enumerate(chunk)]}
            request = {"model": cfg["model"],
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": canonical(payload)}],
                       "response_format": {"type": "json_schema", "json_schema": schema()},
                       "max_completion_tokens": 20 * len(chunk) + 200, "store": False}
            if cfg["model"].startswith(("gpt-4.1", "gpt-4o")):
                request["temperature"] = 0
            batch = {"batch_id": f"{sec['accession']}|{sec['section_key']}|headings_{part}",
                     "heading_ids": [h["heading_id"] for h in chunk], "texts": [h["text"] for h in chunk]}
            key = {"runner_version": RUNNER_VERSION, "request": request}
            out.append({"batch": batch, "request": request, "request_hash": digest(canonical(key)),
                        "identity": {"runner_version": RUNNER_VERSION, "request": request, "batch": batch}})
    return out


def answers(plan: dict, record: dict) -> tuple[list[str], dict[str, int]]:
    """Problems with a reply, and heading id -> level for the ids it answered once.

    A level more than one deeper than the heading before it is cut back to that + 1, so the
    path never skips a level.
    """
    from .label import parse_reply
    problems, parsed = parse_reply(record["raw_response"], schema()["schema"], "results",
                                   duplicate_keys_ok=False)
    if problems:
        return problems, {}
    ids = plan["batch"]["heading_ids"]
    got: dict[str, list[int]] = {}
    for row in parsed["results"]:
        got.setdefault(row["id"], []).append(row["level"])
    out, last = {}, 0
    for k, hid in enumerate(ids):
        answer = got.get(f"h{k + 1}")
        if not answer or len(answer) != 1:
            problems.append(f"h{k + 1}: no single answer (read as a sibling)")
            continue
        level = answer[0]
        if level > last + 1:
            level = last + 1
        out[hid] = level
        last = level or last
    return problems, out


def run(dry_run: bool = False, limit: int | None = None) -> None:
    from .label import Labeller, StopRun, reserve_cost
    cfg = settings()["label"]
    sections = section_headings()
    todo = plans(sections, cfg)
    n_heads = sum(len(s["headings"]) for s in sections)
    log(STEP, f"{len(sections)} sections with {n_heads} sub-headings, {len(todo)} requests "
              f"(about ${sum(reserve_cost(p['request'], cfg) for p in todo):.2f} at most)")
    if dry_run:
        write_jsonl(data_dir() / "interim" / "heading_candidates.jsonl", sections)
        return
    labeller, sent, levels, texts = Labeller(cfg), 0, {}, {}
    try:
        for plan in todo:
            record = labeller.cached(plan, "heading_levels")
            if record is None:
                if limit is not None and sent >= limit:
                    continue
                record = labeller.send(plan, "heading_levels")
                sent += 1
            problems, got = answers(plan, record)
            for p in problems:
                log(STEP, f"{plan['batch']['batch_id']}: {p}")
            levels.update(got)
            texts.update(zip(plan["batch"]["heading_ids"], plan["batch"]["texts"]))
    except StopRun as exc:
        log(STEP, f"stopped: {exc}")
    rows = [{"heading_id": h, "level": lv, "text": texts[h], "source": f"llm:{cfg['model']}:{RUNNER_VERSION}"}
            for h, lv in levels.items()]
    write_jsonl(levels_path(), rows)
    log(STEP, f"{sent} requests sent; {len(rows)} of {n_heads} headings have a level -> {levels_path()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
