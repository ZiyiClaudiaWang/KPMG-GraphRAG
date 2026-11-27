"""Diagnostics for steps 2-4 at scale: how the hand-written rules behave on every filing.

  python -m src.diagnose            read data/sections/*.jsonl, write data/checks/diagnose/

No API calls, no database, and no pipeline output is touched: steps 2-4 run in memory on
EVERY section, not only the kept ones, so the report also shows how the rules would do on
sections the filter drops today. Four of them are reported on their own as candidates
(CANDIDATES below); financial_statements also gets a profile of what it holds (statement
tables, the auditor's report, each note).

Outputs (under data/checks/diagnose/):
  report_steps_2_4.md   the summary: read this first
  samples.csv           examples for each check, kept sections and candidates first
  per_filing.csv        counts per filing (kept sections), to sort and compare
  per_section_type.csv  counts per section_type, all sections

Rule ids: C = step 2 clean, F = step 3 filter, S = step 4 segment (README rule table).
"""

from __future__ import annotations

import random
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from . import clean as C
from . import segment as S
from .common import Paths, checks_dir, filings, load_config, log, read_jsonl, settings, write_csv
from .filter_sections import decide, text_stats

STEP = "diagnose"

# Sections dropped today that someone has asked to add. Each gets its own column.
CANDIDATES = ["financial_statements", "market_risk", "legal_proceedings", "cybersecurity"]

# The 20 filings the rules were written on (README, Data Scope).
BASELINE_20 = {
    "0000320193-25-000079", "0000320193-26-000013", "0000320193-26-000020", "0000726958-26-000046",
    "0000726958-26-000085", "0000927653-26-000069", "0000927653-26-000234", "0001628280-26-044354",
    "0001628280-26-060473", "0001193125-26-191507", "0001193125-26-323660", "0000723125-26-000015",
    "0000723125-26-000023", "0000320187-26-000088", "0000320187-26-000193", "0001193125-26-259683",
    "0000076334-26-000073", "0000076334-26-000105", "0001628280-26-037434", "0001628280-26-054870",
}

SAMPLE_CAP = {"keep": 10, "candidate": 6, "other": 2}   # examples per check, per company
SNIPPET = 240

# ---------------------------------------------------------------- patterns ----

# A table row as the Data team exports it: cells joined by " | ".
TABLE_ROW = re.compile(r"\s\|\s|^\||\|$")
# Table titles and column headers: "Three Months Ended", "September 30,", "(In millions)".
MONTH = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
TABLE_TITLE = re.compile(
    rf"^(?:\(?(?:in|dollars in|amounts in)\s+(?:millions|thousands|billions)"
    rf"|(?:(?:three|six|nine|twelve|fiscal)\s+)?(?:months|weeks|years?|quarters?)\s+ended"
    rf"|period\s+end(?:ing|ed)|as of\b|{MONTH}\s+\d{{1,2}},?(?:\s+\d{{4}})?,?$|\d{{4}}(?:\s+vs\.?\s+\d{{4}})?$)",
    re.IGNORECASE)
# Leftover page furniture: a short line naming the form, or "Page 12".
FURNITURE = re.compile(r"form\s+10-[kq]\b|^page\s+\d+", re.IGNORECASE)
# A brand written in lower case ("iPhone", "iPad", "eBay"): not a missed join.
BRAND = re.compile(r"^[a-z]{1,3}[A-Z]")
# U.S./Inc./... followed by a capitalised word, inside one unit (S2 did not split).
ABBR_FOLLOW = re.compile(r"\b(U\.S|U\.K|E\.U|Inc|Corp|Co|Ltd|LLC|etc|plc)\.\s+([A-Z][A-Za-z]+)")
ABBR_END = re.compile(r"\b(U\.S|U\.K|E\.U|Inc|Corp|Co|Ltd|LLC|etc|plc)\.$")
# Financial statements: where each part starts.
NOTE_HEAD = re.compile(r"^(?:note\s+\d{1,2}[a-z]?\b[\s.:–—-]*|\d{1,2}\.\s*(?=[A-Z]))", re.IGNORECASE)
AUDITOR = re.compile(r"report of independent registered public accounting firm", re.IGNORECASE)
STATEMENTS = re.compile(r"^(?:condensed\s+)?consolidated\s+(?:statements?|balance\s+sheets?)", re.IGNORECASE)
TABLE_WORDS = re.compile(r"\b(?:table|chart|graph|summar(?:y|ized|izes))\b|as follows", re.IGNORECASE)


# ---------------------------------------------------------------- checks ----

@dataclass(frozen=True)
class Check:
    id: str
    rule: str
    meaning: str
    kind: str = "problem"     # "problem": a likely error; "info": how often a rule fires


CHECKS = [
    Check("C9_gray_no_punct_capital", "C9", "Prose paragraph with no end punctuation, next starts upper case: a page break the rules cannot decide. Joined only if step 2b said yes; counted here when not joined."),
    Check("C9_gray_short_prev_lower", "C9", "Paragraph under 8 words, next starts lower case; not joined. Usually a heading ('iPhone' / 'iPhone net sales'), sometimes a cut sentence."),
    Check("C9_join_table_row", "C9", "A joined boundary where either side looks like a table row (any ' | '): has_table_cells() and this check disagree."),
    Check("C7_leftover_furniture", "C2-C7", "Short line naming the form ('FORM 10-Q') or a page, not removed (cross-references such as 'Refer to ... of this Form 10-Q' left out)."),
    Check("C7_repeated_in_section", "C7", "Short prose line repeated 3+ times in one section, not removed (C7 counts repeats across sections only)."),
    Check("S1_starts_lowercase", "S1, C9", "Unit sent to the LLM starts lower case, not from a list item (brands such as 'iPhone' and bullet or lettered items left out): a missed join or a bad split."),
    Check("S1_no_end_punct", "S1", "Unit sent to the LLM with no end punctuation and no '|': a cut sentence or a missed heading."),
    Check("S1_very_long", "S1", "Unit sent to the LLM over 1,500 characters: a missed split, often a ';' list in one sentence."),
    Check("S15_single_pipe", "S15", "Unit sent to the LLM with one '|': a two-column table row ('Acquisitions | 47'). S15 needs 2+ pipes."),
    Check("S5_long_heading", "S5", "Heading over 100 characters (table titles left out): often a sentence cut by a page break, so it is never sent to the LLM."),
    Check("S5_missed_heading", "S5-S7", "Whole paragraph marked too_short, upper case, no '|': may be a heading the rules missed."),
    Check("S8_heading_after_heading", "S8", "Heading right after another (table titles left out): a parent/child pair; only the child is kept in section_path."),
    Check("S12_lettered_list_item", "S12", "Paragraph opens 'a.', '(b)', '1.' and is not in a list (table footnotes '(1)...' mostly): a list item not read as one, or a footnote."),
    Check("S12_bullet_lost", "S12", "Paragraph after a bullet item that looks like an item ('...;' or lower case) but has no bullet sign, and step 2 did not join it: the export dropped the bullet."),
    Check("S13_bullet_outside_list", "S13", "Unit from a bullet paragraph with no list_intro_id (no ':' intro, heading or 'the following' before the list). Mostly full sentences, which read alone.", "info"),
    Check("S13_fragment_bullet_no_intro", "S13", "Same, and the item is cut ('tariffs;', lower case, no end punctuation): it cannot be read alone."),
    Check("S13_colon_list_without_bullets", "S6, S13", "Paragraph ends in ':' and the next is prose, not a bullet or a table: an unbulleted list."),
    Check("S9_risk_section_no_category", "S9", "Item 1A with 20+ units and no category heading: its Claims get no heading group."),
    Check("S9_unmapped_category", "S9", "Category heading not in config/risk_taxonomy/risk_category_map.csv: step 7a reports it unmapped."),
    Check("S2_split_after_abbr", "S2", "Split right after U.S./Inc./... because the next word is in OPENERS. Check that these are real sentence ends.", "info"),
]
CHECK_IDS = [c.id for c in CHECKS]
CHECK_BY_ID = {c.id: c for c in CHECKS}


# ---------------------------------------------------------------- step 2 ----

BOUNDARY_ORDER = list(C.BOUNDARY_CLASSES) + ["C9_gray_joined_by_llm"]


# ---------------------------------------------------------------- financial statements ----

def fs_parts(paragraphs: list[dict]) -> list[str]:
    """Part of a financial-statements section each paragraph is in: 'statements', 'auditor
    report', or 'note: <title>'."""
    part, out = "statements", []
    for p in paragraphs:
        x = p["text"].strip()
        if len(x) <= 150 and "|" not in x:
            if AUDITOR.search(x):
                part = "auditor report"
            elif STATEMENTS.match(x) and not part.startswith("note"):
                part = "statements"
            elif NOTE_HEAD.match(x) and (S.is_heading_like(x) or len(x.split()) <= 12):
                title = NOTE_HEAD.sub("", x).strip(" .:–—-").lower()
                title = re.sub(r"\s+", " ", re.sub(r"\(continued\)", "", title)).strip()
                part = f"note: {title[:60]}" if title else part
        out.append(part)
    return out


def fs_kind(part: str) -> str:
    return "notes" if part.startswith("note") else part


# ---------------------------------------------------------------- collecting ----

class Collector:
    """Counts every case and keeps an even random sample per company and bucket."""

    def __init__(self) -> None:
        self.samples: dict[tuple, list] = defaultdict(list)
        self.n: Counter = Counter()

    def add(self, check: str, bucket: str, f: dict, sec: dict, text: str,
            before: str = "", after: str = "") -> None:
        group = bucket if bucket in SAMPLE_CAP else "candidate"
        key = (check, bucket, f["ticker"])
        n = self.n[key]
        self.n[key] += 1
        row = {"check": check, "rule": CHECK_BY_ID[check].rule if check in CHECK_BY_ID else "",
               "bucket": bucket, "ticker": f["ticker"], "form": f["form"],
               "reporting_period_end": f.get("reporting_period_end"), "accession": f["accession"],
               "section_type": sec.get("section_type"), "section_key": sec.get("section_key"),
               "text": text[:SNIPPET], "before": before[-SNIPPET:], "after": after[:SNIPPET]}
        cap, bucket_rows = SAMPLE_CAP[group], self.samples[key]
        if len(bucket_rows) < cap:
            bucket_rows.append(row)
        else:
            k = random.randrange(n + 1)
            if k < cap:
                bucket_rows[k] = row

    def rows(self) -> list[dict]:
        order = {c: i for i, c in enumerate(CHECK_IDS)}
        border = {"keep": 0, **{c: i + 1 for i, c in enumerate(CANDIDATES)}, "other": 9}
        keys = sorted(self.samples, key=lambda k: (order.get(k[0], 99), border.get(k[1], 9), k[2]))
        return [r for k in keys for r in self.samples[k]]

    def example(self, check: str, bucket: str = "keep") -> str:
        for (c, b, _), rows in sorted(self.samples.items()):
            if c == check and b == bucket and rows:
                r = rows[0]
                return f"{r['ticker']}: {r['text'][:90]}"
        return ""


def md_table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def rate(n: int, d: int) -> str:
    return f"{1000 * n / d:.1f}" if d else "-"


def bucket_of(stype: str, decision: str) -> str:
    if decision == "keep":
        return "keep"
    return stype if stype in CANDIDATES else "other"


# ---------------------------------------------------------------- main ----

def run() -> None:
    random.seed(0)
    cfg_clean, cfg_seg = settings()["clean"], settings()["segment"]
    rules = load_config("sections")
    from .risk_taxonomy import category_key, category_map
    mapped = set(category_map())

    from .page_breaks import load_decisions
    decisions = load_decisions()
    col = Collector()
    hits: dict[str, Counter] = defaultdict(Counter)        # bucket -> check -> n
    units_in: Counter = Counter()                          # bucket -> units
    llm_in: Counter = Counter()                            # bucket -> units sent to the LLM
    by_type: dict[str, Counter] = defaultdict(Counter)     # section_type -> stats
    type_decision: dict[str, str] = {}
    boundaries: dict[str, Counter] = defaultdict(Counter)  # bucket -> class -> n
    removed: Counter = Counter()
    baseline: dict[str, Counter] = {"baseline 20": Counter(), "other filings": Counter()}
    fs_units: dict[str, Counter] = defaultdict(Counter)    # fs kind -> units/llm/table_row
    fs_hits: dict[str, Counter] = defaultdict(Counter)     # fs kind -> check -> n
    fs_notes: dict[str, Counter] = defaultdict(Counter)    # note title -> llm / units
    fs_note_filings: dict[str, set] = defaultdict(set)
    abbr_follow, abbr_split = Counter(), Counter()
    unmapped: Counter = Counter()
    unknown_types: Counter = Counter()
    risk_stubs = 0
    missing, per_filing = [], []

    for f in filings():
        path = Paths.sections(f)
        if not path.exists():
            missing.append(f"{f['ticker']} {f['form']} {f['accession']}")
            continue
        sections = list(read_jsonl(path))
        headers = C.repeated_headers(sections, cfg_clean)
        row = Counter()
        base_group = "baseline 20" if f["accession"] in BASELINE_20 else "other filings"

        for sec in sections:
            if not (sec.get("content") or "").strip():
                continue
            cleaned = C.clean_section(sec, headers, decisions)
            decision, _ = decide(cleaned, rules)
            stype = cleaned["section_type"] or "None"
            if stype not in rules["keep"] and stype not in rules["drop"] and stype not in (rules.get("review") or {}):
                unknown_types[stype] += 1
            bucket = bucket_of(stype, decision)
            type_decision[stype] = decision
            st = text_stats(cleaned)
            T = by_type[stype]
            T["sections"] += 1
            T["chars"] += st["n_chars"]
            T["table_chars"] += round(st["table_share"] * st["n_chars"])
            paras = cleaned["paragraphs"]
            parts = fs_parts(paras) if stype == "financial_statements" else None

            def hit(check: str, text: str, p: int | None = None, before: str = "", after: str = "") -> None:
                hits[bucket][check] += 1
                T[check] += 1
                if bucket == "keep":
                    row[check] += 1
                    baseline[base_group][check] += 1
                if parts is not None and p is not None:
                    fs_hits[fs_kind(parts[p])][check] += 1
                col.add(check, bucket, f, cleaned, text, before, after)

            # ---- step 2
            for r in cleaned["removed"]:
                removed[r["rule"]] += 1
            for b in C.walk(sec, headers, decisions)[2]:
                cls, before, after, joined, prev_item = b["class"], b["prev"], b["next"], b["joined"], b["prev_is_item"]
                boundaries[bucket][cls] += 1
                if cls.startswith("C9_") and not joined:
                    hit(cls, f"{before[-70:]}  ⟶  {after[:90]}", before=before, after=after)
                if cls.startswith("C9_") and joined:
                    boundaries[bucket]["C9_gray_joined_by_llm"] += 1
                if joined and (TABLE_ROW.search(before) or TABLE_ROW.search(after)):
                    hit("C9_join_table_row", f"{before[-70:]}  ⟶  {after[:90]}", before=before, after=after)
                if (prev_item and not joined and not C.is_item_start(after) and "|" not in after
                        and not BRAND.match(after) and not S.is_heading_like(after)
                        and (after[:1].islower() or after.rstrip().endswith((";", "; and", "; or")))):
                    hit("S12_bullet_lost", after, None, before, after)
            texts = [p["text"] for p in paras]
            for k, x in enumerate(texts):
                if len(x) <= 120 and "|" not in x and FURNITURE.search(x) and not S.CROSS_REFERENCE.search(x):
                    hit("C7_leftover_furniture", x, k)
            for x, n in Counter(x for x in texts if len(x) <= C.CONTINUED_HEADER_MAX_CHARS).items():
                if (n >= 3 and not C.TERMINAL.search(x) and not TABLE_ROW.search(x) and not TABLE_TITLE.match(x)
                        and not S.is_bullet(x) and S.note_key(x) not in S.TABLE_NOTE_KEYS):
                    hit("C7_repeated_in_section", f"{x}  (x{n})")

            # ---- step 4, units
            units = S.segment_section(cleaned, cfg_seg)
            listed_paras: set[int] = set()
            n_cat = 0
            prev_u = None
            for u in units:
                text, p = u["text"], u["paragraph_index"]
                to_llm = u["prefilter"] is None
                units_in[bucket] += 1
                llm_in[bucket] += to_llm
                T["units"] += 1
                T["llm"] += to_llm
                if bucket == "keep":
                    row["units"] += 1
                    row["to_llm"] += to_llm
                    baseline[base_group]["units"] += 1
                if parts is not None:
                    kind = fs_kind(parts[p])
                    fs_units[kind]["units"] += 1
                    fs_units[kind]["llm"] += to_llm
                    fs_units[kind]["table_row"] += bool(TABLE_ROW.search(text))
                    if kind == "notes":
                        title = parts[p][6:]
                        fs_notes[title]["units"] += 1
                        fs_notes[title]["llm"] += to_llm
                        fs_note_filings[title].add(f["accession"])
                before = paras[p - 1]["text"] if p > 0 else ""
                after = paras[p + 1]["text"] if p + 1 < len(paras) else ""
                in_list = "list_intro_id" in u or u["role"] == "list_group"
                from_item = bool(S.item_marker(paras[p]["text"]))
                if in_list:
                    listed_paras.update(u.get("list_paragraphs") or [p])
                if (to_llm and not in_list and not from_item and text[:1].islower() and not BRAND.match(text)
                        and not C.ITEM_MARKER.match(text)):
                    hit("S1_starts_lowercase", text, p, before, after)
                if u["role"] == "body" and not in_list and S.is_bullet(paras[p]["text"]):
                    hit("S13_bullet_outside_list", text, p, before, after)
                    item = S.BULLET.sub("", paras[p]["text"])
                    if item[:1].islower() or item.rstrip().endswith((";", ",")) or not S.TERMINAL.search(item):
                        hit("S13_fragment_bullet_no_intro", text, p, before, after)
                if (to_llm and not in_list and not S.TERMINAL.search(text) and not text.endswith(":")
                        and not u.get("rule_heading") and "|" not in text):
                    hit("S1_no_end_punct", text, p, before, after)
                if to_llm and len(text) > 1500:
                    hit("S1_very_long", text, p, before, after)
                if to_llm and text.count("|") == 1:
                    hit("S15_single_pipe", text, p, before, after)
                if u["role"] == "heading" and len(text) > 100 and not TABLE_TITLE.match(text):
                    hit("S5_long_heading", text, p, before, after)
                if (u["prefilter"] == "too_short" and text.strip() == paras[p]["text"].strip()
                        and text[:1].isupper() and "|" not in text and not TABLE_TITLE.match(text)):
                    hit("S5_missed_heading", text, p, before, after)
                if (u["role"] == "heading" and prev_u is not None
                        and prev_u["role"] == "heading"
                        and prev_u["paragraph_index"] == p - 1
                        and not TABLE_TITLE.match(text) and not TABLE_TITLE.match(prev_u["text"])):
                    hit("S8_heading_after_heading", f"{prev_u['text']}  >  {text}", p, before, after)
                if S.is_category_unit(u):
                    n_cat += 1
                    key = category_key(text)
                    if stype == "risk_factors" and key not in mapped:
                        unmapped[f"{f['ticker']}: {text.rstrip(':').strip()}"] += 1
                        hit("S9_unmapped_category", text, p)
                if u["role"] == "body":
                    for m in ABBR_FOLLOW.finditer(text):
                        abbr_follow[m.group(2)] += 1
                    if prev_u is not None and prev_u["role"] == "body" and ABBR_END.search(prev_u["text"]):
                        first = text.split()[0].strip("\"“(").rstrip(".,;:") if text.split() else ""
                        abbr_split[first] += 1
                        hit("S2_split_after_abbr", f"{prev_u['text'][-80:]}  ||  {text[:120]}", p)
                prev_u = u

            # ---- step 4, paragraphs (lists)
            for k, x in enumerate(texts):
                prev_x = texts[k - 1] if k else ""
                nxt_x = texts[k + 1] if k + 1 < len(texts) else ""
                if (C.ITEM_MARKER.match(x) and not S.is_bullet(x) and k not in listed_paras
                        and not (x[:1].isdigit() and S.is_heading_like(x))):
                    hit("S12_lettered_list_item", x, k, prev_x, nxt_x)
                if (x.endswith(":") and nxt_x and not S.item_marker(nxt_x) and not S.is_colon_heading(x)
                        and not TABLE_ROW.search(nxt_x) and not TABLE_TITLE.match(nxt_x)
                        and not TABLE_WORDS.search(x) and not S.is_heading_like(nxt_x)):
                    hit("S13_colon_list_without_bullets", x, k, prev_x, nxt_x)

            if stype == "risk_factors":
                row["risk_sections"] += 1
                row["category_headings"] += n_cat
                if n_cat == 0 and len(units) >= 20:
                    hit("S9_risk_section_no_category", f"{len(units)} units: " + " / ".join(t[:60] for t in texts[:3]))
                elif n_cat == 0:
                    risk_stubs += 1

        per_filing.append({"ticker": f["ticker"], "form": f["form"],
                           "reporting_period_end": f.get("reporting_period_end"), "accession": f["accession"],
                           "baseline_20": base_group == "baseline 20", "header_patterns": len(headers), **row})

    # ------------------------------------------------------------ files ----
    out_dir = checks_dir("diagnose")
    out_dir.mkdir(parents=True, exist_ok=True)
    head = ["ticker", "form", "reporting_period_end", "accession", "baseline_20", "header_patterns",
            "units", "to_llm"]
    write_csv(out_dir / "per_filing.csv", per_filing,
              head + [c for c in CHECK_IDS if any(c in r for r in per_filing)]
              + ["risk_sections", "category_headings"])
    type_rows = [{"section_type": t, "decision": type_decision[t], **v} for t, v in sorted(by_type.items())]
    write_csv(out_dir / "per_section_type.csv", type_rows,
              ["section_type", "decision", "sections", "chars", "table_chars", "units", "llm"] + CHECK_IDS)
    write_csv(out_dir / "samples.csv", col.rows(),
              ["check", "rule", "bucket", "ticker", "form", "reporting_period_end", "accession",
               "section_type", "section_key", "text", "before", "after"])

    # ------------------------------------------------------------ report ----
    kept_u, kept_llm = units_in["keep"], llm_in["keep"]
    L = ["# Steps 2-4 diagnostics", "",
         f"{len(per_filing)} of {len(filings())} filings in `config/filings.yaml` have an export"
         + (f"; **missing: {', '.join(missing[:20])}**" if missing else "") + ".", "",
         "**How to read this report**", "",
         "- Steps 2-4 ran in memory on every section. *Kept* = the sections step 3 keeps today "
         "(risk_factors, mda, business); the candidate columns are sections someone asked to add.",
         "- Each check counts *candidates* for an error, not confirmed errors. Rates are per 1,000 "
         "units, so sections of different sizes compare. Read the examples in `samples.csv` "
         "(filter on `check` and `bucket`) before changing a rule.",
         "- Rule ids (C9, S15, ...) are those of the README rule table.", ""]

    # summary
    L += ["## Summary", ""]
    tops = sorted((c for c in CHECKS if c.kind == "problem"), key=lambda c: -hits["keep"][c.id])
    L += [f"- Kept sections: **{kept_u:,} units, {kept_llm:,} sent to the LLM.**"]
    for cand in CANDIDATES:
        if units_in[cand]:
            L += [f"- Adding `{cand}`: +{llm_in[cand]:,} units to the LLM "
                  f"(+{100 * llm_in[cand] / max(kept_llm, 1):.0f}%)."]
    if fs_units:
        notes_llm = fs_units["notes"]["llm"]
        L += [f"  - of which in the notes: {notes_llm:,}; statements: {fs_units['statements']['llm']:,}; "
              f"auditor report: {fs_units['auditor report']['llm']:,} (section 4)."]
    L += ["- Largest checks in kept sections: "
          + "; ".join(f"`{c.id}` {hits['keep'][c.id]:,}" for c in tops[:5] if hits["keep"][c.id]) + ".", ""]

    # 1. sections
    L += ["## 1. Sections (step 3)", ""]
    rows = []
    for stype, T in sorted(by_type.items(), key=lambda kv: (type_decision[kv[0]] != "keep",
                                                             kv[0] not in CANDIDATES, -kv[1]["chars"])):
        mark = " (candidate)" if stype in CANDIDATES else ""
        rows.append([f"{stype}{mark}", type_decision[stype], T["sections"], f"{T['chars']:,}",
                     f"{T['table_chars'] / max(T['chars'], 1):.2f}", f"{T['units']:,}", f"{T['llm']:,}",
                     f"{T['llm'] / max(T['sections'], 1):.0f}"])
    L += [md_table(["section_type", "decision", "sections", "characters", "share in tables", "units",
                    "units to LLM", "to LLM per section"], rows), ""]
    if unknown_types:
        L += ["**Not in `config/sections.yaml` (marked review):** "
              + ", ".join(f"{k} ({v})" for k, v in unknown_types.most_common()), ""]

    # 2. checks
    L += ["## 2. Checks", "",
          "Per 1,000 units; the count for kept sections in brackets. The example is from a kept section.", ""]
    cols = ["keep"] + [c for c in CANDIDATES if units_in[c]]
    rows = []
    for c in CHECKS:
        cells = [f"{rate(hits['keep'][c.id], kept_u)} ({hits['keep'][c.id]:,})"]
        cells += [rate(hits[b][c.id], units_in[b]) for b in cols[1:]]
        tag = " *(info)*" if c.kind == "info" else ""
        rows.append([f"`{c.id}`{tag}", c.rule, c.meaning] + cells + [col.example(c.id)])
    L += [md_table(["check", "rule", "what it means", "kept"] + cols[1:] + ["example"], rows), ""]

    # 3. boundaries
    L += ["## 3. Paragraph boundaries (C9)", "",
          "Every boundary between two paragraphs, by its class (clean.boundary_class). `join_*` "
          "classes are joined, `C9_gray_*` only when step 2b said yes (`C9_gray_joined_by_llm`), "
          "the rest never.", ""]
    rows = [[k] + [f"{boundaries[b][k]:,}" for b in cols] for k in BOUNDARY_ORDER
            if any(boundaries[b][k] for b in cols)]
    L += [md_table(["class"] + cols, rows), ""]

    # 4. financial statements
    if fs_units:
        L += ["## 4. Financial statements: what the section holds", "",
              "Each paragraph is placed in the statements (tables), the auditor's report (10-K), or a "
              "note, by the headings above it ('Note 7 - Debt', '7. Debt').", ""]
        kinds = [k for k in ("statements", "auditor report", "notes") if fs_units[k]["units"]]
        rows = [[k, f"{fs_units[k]['units']:,}", f"{fs_units[k]['table_row']:,}", f"{fs_units[k]['llm']:,}"]
                for k in kinds]
        L += [md_table(["part", "units", "table rows", "units to LLM"], rows), "",
              "Checks by part, per 1,000 units:", ""]
        rows = [[f"`{c.id}`"] + [rate(fs_hits[k][c.id], fs_units[k]["units"]) for k in kinds]
                for c in CHECKS if c.kind == "problem" and any(fs_hits[k][c.id] for k in kinds)]
        L += [md_table(["check"] + kinds, rows), "",
              "Notes with the most units sent to the LLM (titles as written, numbers removed):", ""]
        rows = [[t, len(fs_note_filings[t]), f"{v['units']:,}", f"{v['llm']:,}"]
                for t, v in sorted(fs_notes.items(), key=lambda kv: -kv[1]["llm"])[:30]]
        L += [md_table(["note", "filings", "units", "to LLM"], rows), "",
              f"{len(fs_notes):,} distinct note titles. A note title seen in few filings is often a "
              "mis-read heading; check `samples.csv` before relying on the split.", ""]

    # 5. baseline
    L += ["## 5. The 20 filings the rules were written on vs the rest (kept sections)", "",
          "Per 1,000 units. A rate much higher on the other filings means the rule fits the 20 only.", ""]
    b, o = baseline["baseline 20"], baseline["other filings"]
    rows = [[f"`{c.id}`", rate(b[c.id], b["units"]), rate(o[c.id], o["units"])]
            for c in CHECKS if b[c.id] or o[c.id]]
    L += [md_table(["check", "baseline 20", "other filings"], rows), "",
          f"Units: baseline {b['units']:,}, other filings {o['units']:,}.", ""]

    # 6. outliers
    L += ["## 6. Filings that stand out (kept sections)", "",
          "A check at least 3x its median rate across filings, with 5+ cases. Usually one company's "
          "format: fix it once for that company.", ""]
    rows = []
    for c in CHECKS:
        if c.kind != "problem":
            continue
        rates = [(r.get(c.id, 0) / r["units"], r) for r in per_filing if r.get("units")]
        if not rates:
            continue
        med = statistics.median(x for x, _ in rates)
        flagged = [(x, r) for x, r in rates if r.get(c.id, 0) >= 5 and x >= 3 * max(med, 1e-9)]
        if not flagged:
            continue
        by_t = Counter(r["ticker"] for _, r in flagged)
        worst = max(flagged, key=lambda z: z[0])
        rows.append([f"`{c.id}`", ", ".join(f"{t} ({n})" for t, n in by_t.most_common(4)),
                     f"{1000 * med:.1f}", f"{worst[1]['ticker']} {worst[1]['form']} "
                     f"{worst[1]['reporting_period_end']}: {1000 * worst[0]:.1f}"])
    L += [md_table(["check", "companies (filings flagged)", "median per 1k", "worst filing per 1k"], rows)
          if rows else "None.", ""]

    # 7. Item 1A
    rs = [r for r in per_filing if r.get("risk_sections")]
    L += ["## 7. Item 1A", "",
          f"- Filings with an Item 1A section: {len(rs)}. Short ones with no category (a 10-Q's "
          f"'no material changes'): {risk_stubs}; these need no theme.",
          f"- Item 1A sections with 20+ units and no category heading: {hits['keep']['S9_risk_section_no_category']} "
          "(their Claims get no theme).",
          f"- Category headings not in `config/risk_taxonomy/risk_category_map.csv`: {len(unmapped)} distinct texts.", ""]
    if unmapped:
        L += [md_table(["category text", "times"], [[k, v] for k, v in unmapped.most_common(30)]), ""]

    # 8. abbreviations
    L += ["## 8. Abbreviations (S2)", "",
          "Words after U.S./Inc./... inside one unit (not split). A word that often starts a sentence "
          "here would be a missed split.", "",
          md_table(["next word", "times"], [[k, v] for k, v in abbr_follow.most_common(20)]), "",
          "First words of units that were split after an abbreviation:", "",
          md_table(["first word", "times"], [[k, v] for k, v in abbr_split.most_common(12)]), ""]

    # 9. removals
    L += ["## 9. Paragraphs removed by step 2 (all sections)", "",
          md_table(["rule", "paragraphs"], [[k, f"{v:,}"] for k, v in removed.most_common()]), "",
          "## Files", "",
          "- `samples.csv`: examples per check; `bucket` is keep, a candidate section, or other. "
          f"Up to {SAMPLE_CAP['keep']} per company for kept sections, {SAMPLE_CAP['candidate']} for "
          f"candidates, {SAMPLE_CAP['other']} for the rest.",
          "- `per_filing.csv`: counts per filing in kept sections.",
          "- `per_section_type.csv`: counts per section_type, all sections.", ""]

    report = out_dir / "report_steps_2_4.md"
    report.write_text("\n".join(L), encoding="utf-8")
    log(STEP, f"{len(per_filing)} filings; kept sections {kept_u:,} units -> {report}")


if __name__ == "__main__":
    run()
