"""Optional EDGAR-backed step 4b. Run from extraction_pipeline.

python -m src.html_heading_levels --llm-levels data/interim/heading_levels_llm.jsonl --fetch
Add --apply after reviewing the preview, then rerun segment and dry-run label.
The baseline is a separate snapshot of step 4b output. No LLM requests are sent.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from lxml import etree, html

from .common import Paths, data_dir, filing_stem, filings, load_env, log, read_jsonl
from .ex21 import edgar
from .heading_levels import levels_path, section_headings

BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "center", "blockquote",
              "table", "tr", "ul", "ol", "body", "section", "article", "dl", "dt", "dd"}
CELL_TAGS = {"td", "th"}
SKIP_TAGS = {"script", "style", "head", "title", "ix:header"}
INHERITED = ("font-weight", "font-style", "font-size", "text-transform", "text-decoration",
             "text-align", "font-variant")

# --------------------------------------------------------------------------- CSS


def parse_style(s: str | None) -> dict[str, str]:
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip().lower()
    return out


def class_rules(tree) -> dict[str, dict[str, str]]:
    """Simple `.cls { ... }` rules from <style> blocks (some filer agents use classes)."""
    rules: dict[str, dict[str, str]] = {}
    for st in tree.iter("style"):
        for name, body in re.findall(r"\.([\w-]+)\s*\{([^}]*)\}", st.text or ""):
            rules.setdefault(name, {}).update(parse_style(body))
    return rules


def to_pt(v: str | None, base: float = 10.0) -> float | None:
    if not v:
        return None
    m = re.match(r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(pt|px|em|rem|%|in)?", v)
    if not m:
        return None
    x, unit = float(m.group(1)), m.group(2) or "px"
    return {"pt": x, "px": x * 0.75, "em": x * base, "rem": x * 10.0, "%": x / 100 * base,
            "in": x * 72}[unit]


def is_bold(w: str | None) -> bool:
    if not w:
        return False
    return w in ("bold", "bolder") or (w.isdigit() and int(w) >= 600)


# --------------------------------------------------------------------------- blocks


@dataclass
class Run:
    text: str
    bold: bool
    italic: bool
    underline: bool
    size: float
    caps: bool  # text-transform uppercase or small-caps


@dataclass
class Block:
    idx: int
    runs: list[Run] = field(default_factory=list)
    align: str = ""
    indent: float = 0.0
    in_table: bool = False
    page_break: bool = False
    start: int = -1          # span in the export text (raw coordinates), -1 = not found
    end: int = -1

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


class Walker:
    def __init__(self, tree):
        self.rules = class_rules(tree)
        self.blocks: list[Block] = []
        self.cur: Block | None = None
        self.pending_break = False

    def style_of(self, el, parent: dict) -> dict:
        st = {k: v for k, v in parent.items() if k in INHERITED or k.startswith("_")}
        own: dict[str, str] = {}
        for cls in (el.get("class") or "").split():
            own.update(self.rules.get(cls, {}))
        own.update(parse_style(el.get("style")))
        st.update(own)
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in ("b", "strong"):
            st["font-weight"] = "bold"
        if tag in ("i", "em"):
            st["font-style"] = "italic"
        if tag == "u":
            st["text-decoration"] = "underline"
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            st["font-weight"] = "bold"
        # font-size relative to the parent
        base = parent.get("_size", 10.0)
        sz = to_pt(own.get("font-size"), base) if "font-size" in own else None
        st["_size"] = sz if sz else base
        return st

    def open_block(self, el, st, parent_indent: float, in_table: bool):
        self.flush()
        ind = parent_indent
        for k in ("margin-left", "padding-left"):
            ind += to_pt(st.get(k)) or 0.0
        ti = to_pt(st.get("text-indent")) or 0.0
        b = Block(len(self.blocks), align=st.get("text-align", ""), indent=ind + max(ti, 0.0),
                  in_table=in_table)
        if self.pending_break:
            b.page_break, self.pending_break = True, False
        self.cur = b
        return ind

    def flush(self):
        if self.cur is not None and self.cur.text.strip():
            self.blocks.append(self.cur)
            self.cur.idx = len(self.blocks) - 1
        self.cur = None

    def add(self, text: str | None, st: dict, indent: float, in_table: bool):
        if not text:
            return
        if self.cur is None:
            self.cur = Block(len(self.blocks), align=st.get("text-align", ""), indent=indent,
                             in_table=in_table)
        caps = st.get("text-transform") == "uppercase" or st.get("font-variant") == "small-caps"
        self.cur.runs.append(Run(text, is_bold(st.get("font-weight")),
                                 st.get("font-style") == "italic",
                                 "underline" in st.get("text-decoration", ""),
                                 round(st.get("_size", 10.0), 1), caps))

    def walk(self, el, parent: dict, indent: float = 0.0, in_table: bool = False):
        tag = el.tag if isinstance(el.tag, str) else ""
        if not tag:  # comment / processing instruction
            return
        tag = tag.lower()
        if tag in SKIP_TAGS:
            return
        st = self.style_of(el, parent)
        if st.get("display") == "none":
            return
        brk = any("page" in st.get(k, "") or st.get(k, "") == "always"
                  for k in ("page-break-before", "break-before"))
        if brk or tag == "hr":
            self.pending_break = True
        block = tag in BLOCK_TAGS and not (in_table and tag in ("div", "p"))
        if tag == "table":
            in_table = True
        child_indent = indent
        if block and tag not in ("table",):
            child_indent = self.open_block(el, st, indent, in_table)
        elif tag in CELL_TAGS and self.cur is not None and self.cur.text.strip():
            self.add(" | ", st, indent, in_table)
        elif tag == "br":
            self.add(" ", st, indent, in_table)
        self.add(el.text, st, child_indent, in_table)
        for ch in el:
            self.walk(ch, st, child_indent, in_table)
            # text after a child belongs to this element
            if isinstance(ch.tag, str) and ch.tag.lower() in BLOCK_TAGS and block:
                self.flush()
            self.add(ch.tail, st, child_indent, in_table)
        if block:
            self.flush()
        if any("page" in st.get(k, "") or st.get(k, "") == "always"
               for k in ("page-break-after", "break-after")):
            self.pending_break = True


def html_blocks(path: Path) -> list[Block]:
    tree = html.parse(str(path), parser=html.HTMLParser(encoding="utf-8", remove_comments=True))
    w = Walker(tree)
    w.walk(tree.getroot(), {"_size": 10.0})
    w.flush()
    return w.blocks


# --------------------------------------------------------------------------- alignment

TRANS = str.maketrans({" ": " ", "‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", "‑": "-", "​": "", "|": " ",
                       "•": " ", "●": " ", "·": " ", "▪": " "})


def norm_with_map(s: str) -> tuple[str, list[int]]:
    """Text without whitespace (and with quotes/dashes unified), and each char's raw index."""
    out, idx = [], []
    for i, ch in enumerate(s):
        ch = ch.translate(TRANS)
        if not ch or ch.isspace():
            continue
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def norm(s: str) -> str:
    return "".join(ch for ch in s.translate(TRANS) if not ch.isspace())


def export_text(sections: list[dict]) -> str:
    """The whole export text in its own coordinates (sections joined at their offsets)."""
    end = max(s["offset_start"] + len(s["content"]) for s in sections)
    buf = [" "] * end
    for s in sections:
        c = s["content"]
        buf[s["offset_start"]:s["offset_start"] + len(c)] = c
    return "".join(buf)


def align(blocks: list[Block], text: str) -> None:
    n, idx = norm_with_map(text)
    cursor = 0
    for b in blocks:
        bn = norm(b.text)
        if len(bn) < 1:
            continue
        window = max(4000, 4 * len(bn))
        pos = n.find(bn, cursor, cursor + window + len(bn))
        if pos < 0:
            continue
        # A repeated block after a gap can be a TOC or an earlier copy of a title.
        if pos != cursor and n.find(bn, pos + 1, cursor + window + len(bn)) >= 0:
            continue
        b.start, b.end = idx[pos], idx[pos + len(bn) - 1] + 1
        cursor = pos + len(bn)


# --------------------------------------------------------------------------- headings


def style_over(block: Block, n_chars: int) -> dict:
    """Typography of the first n_chars (whitespace not counted) of a block."""
    got, c = Counter(), 0
    sizes = Counter()
    for r in block.runs:
        k = len(norm(r.text))
        if k == 0:
            continue
        take = min(k, n_chars - c)
        if take <= 0:
            break
        for name in ("bold", "italic", "underline", "caps"):
            if getattr(r, name):
                got[name] += take
        sizes[r.size] += take
        c += take
    tot = max(c, 1)
    return {"bold": got["bold"] / tot >= 0.8, "italic": got["italic"] / tot >= 0.8,
            "underline": got["underline"] / tot >= 0.8, "caps_css": got["caps"] / tot >= 0.8,
            "size": sizes.most_common(1)[0][0] if sizes else 10.0}


USE_INDENT = frozenset({"MCK"})


def signature(h_text: str, block: Block, how: str, ticker: str = "") -> tuple:
    st = style_over(block, len(norm(h_text)))
    letters = [ch for ch in h_text if ch.isalpha()]
    caps = st["caps_css"] or (bool(letters) and all(ch.isupper() for ch in letters))
    return (st["size"], st["bold"], st["underline"], caps, st["italic"],
            block.align == "center", indent_bucket(block, how, ticker), how == "run_in", block.in_table)


def indent_bucket(block: Block, how: str, ticker: str) -> int:
    """Left indent in 9pt steps; 0 for run-in lead-ins (that indent is the paragraph's first
    line) and for companies where indent is not a level (USE_INDENT)."""
    if how == "run_in" or ticker not in USE_INDENT:
        return 0
    return round(block.indent / 9) * 9


SIG_COLS = ["size", "bold", "underline", "caps", "italic", "center", "indent", "run_in", "in_table"]


def score(sig: tuple) -> float:
    """How prominent a heading style looks: size first, then bold / underline / capitals /
    centred; italic and run-in lower it; indent breaks ties."""
    size, bold, under, caps, ital, center, indent, runin, tbl = sig
    return size * 10 + bold + under + caps + center - 0.5 * ital - 2 * runin - indent / 100


ITEM_TITLE = re.compile(r"^item\s*\d+[a-z]?\b", re.I)
CONTINUATION = re.compile(r"^(?:for the |ended |as of )", re.I)


def primary_url(filing: dict) -> str:
    accession = filing["accession"]
    base = f"{edgar.ARCHIVES}/{int(filing['cik'])}/{accession.replace('-', '')}/"
    index = edgar._IndexRows()
    index.feed(edgar.get(f"{base}{accession}-index.html").decode("utf-8", "replace"))
    urls = set()
    for cells, href in index.rows:
        if not href or len(cells) < 4 or cells[0] != "1" or cells[3] != filing["form"]:
            continue
        href = href.removeprefix("/ix?doc=")
        path = urlsplit(href).path
        url = f"https://www.sec.gov{path}" if path.startswith("/") else base + path
        if url.startswith(base) and re.fullmatch(r"[\w.-]+\.html?", url[len(base):], re.I):
            urls.add(url)
    if len(urls) != 1:
        raise ValueError(f"Expected one primary HTML document for {accession}")
    return urls.pop()


def filing_html(filing: dict, directory: Path, fetch: bool) -> Path:
    path = directory / f"{filing_stem(filing)}.htm"
    if not path.is_file() and fetch:
        url = primary_url(filing)
        raw = edgar.get(url)
        if not html.fromstring(raw).xpath("//body"):
            raise ValueError(f"No HTML body in {url}")
        atomic_write(path, lambda stream: stream.write(raw), binary=True)
    return path


def atomic_write(path: Path, write, *, binary: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb" if binary else "w", encoding=None if binary else "utf-8",
        dir=path.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            write(stream)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def match_heading(heading: dict, section: dict, blocks: list[Block],
                  starts: list[int], text: str, ticker: str) -> dict:
    position = section["offset_start"] + int(heading["heading_id"].rsplit("|", 1)[1])
    index = bisect.bisect_right(starts, position) - 1
    if index < 0 or position >= blocks[index].end:
        return {"match": "unmatched"}
    block = blocks[index]
    title = norm(heading["text"])
    prefix = norm(text[block.start:position])
    block_text = norm(block.text)
    if not title or not block_text[len(prefix):].startswith(title):
        return {"match": "unmatched"}
    how = "inside" if prefix else "standalone" if block_text == title else "run_in"
    style = signature(heading["text"], block, how, ticker) if not prefix else None
    return {
        "match": how, "html_block_index": block.idx,
        "style": dict(zip(SIG_COLS, style)) if style else None,
        "in_table": block.in_table,
        "block_text": block.text[:160],
    }


def infer_section(group: dict, section: dict | None, blocks: list[Block],
                  text: str, baseline: dict, sha: str | None) -> list[dict]:
    starts = [b.start for b in blocks]
    stack = []
    title_run = False
    previous_block = next(
        (b for b in blocks if section and b.start >= section["offset_start"]), None
    )
    if previous_block and ITEM_TITLE.match(previous_block.text.strip()):
        title_run = True
    rows = []
    for candidate in group["headings"]:
        key = candidate["heading_id"]
        prior = baseline.get(key)
        evidence = match_heading(candidate, section, blocks, starts, text, group["ticker"]) if section else {
            "match": "missing_html_or_section",
        }
        style = evidence.get("style")
        sig = tuple(style.values()) if style else None
        block_index = evidence.get("html_block_index")
        continuation = (
            title_run and previous_block is not None and block_index == previous_block.idx + 1
            and style and style["center"] and CONTINUATION.match(candidate["text"])
        )
        title_run = bool(continuation)
        if continuation:
            previous_block = next(b for b in blocks if b.idx == block_index)

        parent = None
        if evidence.get("in_table") or continuation:
            level, source = 0, "html"
            reason = "table" if evidence.get("in_table") else "item_title_continuation"
        elif prior and prior["level"] in (-1, 0):
            level, source, reason = prior["level"], prior["source"], "llm_role"
        elif sig and (evidence["match"] == "standalone" or style["bold"] or style["italic"]):
            if sig[3] and len(candidate["text"].split()) == 1 and stack:
                plain = sig[:3] + (False,) + sig[4:]
                if stack[-1][2] == plain:
                    sig = plain
            for index, (_, _, open_style) in enumerate(stack):
                if open_style == sig:
                    del stack[index:]
                    break
            else:
                while stack and stack[-1][2] is not None and score(stack[-1][2]) < score(sig):
                    stack.pop()
            level = stack[-1][0] + 1 if stack else 1
            source, reason = "html", "typography"
        else:
            level = prior["level"] if prior else (stack[-1][0] if stack else 1)
            source = prior["source"] if prior else "segment_default"
            reason = "llm_fallback" if prior else "missing_llm_fallback"

        if level > 0:
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            stack.append((level, key, sig if source == "html" else None))
        rows.append({
            "heading_id": key, "text": candidate["text"], "level": level, "source": source,
            "reason": reason, "parent_heading_id": parent, "level_4b": prior["level"] if prior else None,
            "html_sha256": sha, **evidence,
        })
    return rows


def run(baseline_path: Path, directory: Path, fetch: bool, apply: bool) -> None:
    load_env()
    destination = levels_path() if apply else levels_path().with_name("heading_levels_html_preview.jsonl")
    if baseline_path.resolve() == destination.resolve():
        raise ValueError("Use a separate step 4b baseline snapshot")
    baseline = {row["heading_id"]: row for row in read_jsonl(baseline_path)}
    if any(row.get("source") in {"html", "segment_default"} for row in baseline.values()):
        raise ValueError("Use a baseline from the original step 4b output")
    for row in baseline.values():
        row["level"] = int(row["level"])
        row.setdefault("source", "llm_baseline")
    groups = defaultdict(list)
    for group in section_headings():
        groups[group["accession"]].append(group)
    if not groups:
        raise ValueError("No step 4b candidates. Run clean and filter first.")
    rows = []
    for filing in filings():
        accession = filing["accession"]
        if accession not in groups:
            continue
        sections, blocks, text, sha = {}, [], "", None
        try:
            path = filing_html(filing, directory, fetch)
            if path.is_file() and Paths.sections(filing).is_file():
                sections = {row["section_key"]: row for row in read_jsonl(Paths.sections(filing))}
                text = export_text(list(sections.values()))
                blocks = html_blocks(path)
                align(blocks, text)
                blocks = [block for block in blocks if block.start >= 0]
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                log("html headings", f"{accession}: missing HTML or section export")
        except (OSError, ValueError, etree.LxmlError, edgar.MissingUserAgent) as exc:
            log("html headings", f"{accession}: {exc}")
            sections, blocks, text, sha = {}, [], "", None
        for group in groups[accession]:
            rows.extend(infer_section(group, sections.get(group["section_key"]), blocks, text, baseline, sha))
    if apply and not any(row["source"] == "html" for row in rows):
        raise ValueError("No HTML decisions to apply. Check the preview and HTML cache.")

    def write_rows(stream):
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    atomic_write(destination, write_rows)
    log("html headings", f"{len(rows)} candidates -> {destination}")
    for reason, count in Counter(row["reason"] for row in rows).most_common():
        log("html headings", f"{reason}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-levels", type=Path, required=True, help="Step 4b baseline snapshot")
    parser.add_argument("--htm-dir", type=Path, default=data_dir() / "edgar" / "primary")
    parser.add_argument("--fetch", action="store_true", help="Download missing EDGAR primary documents")
    parser.add_argument("--apply", action="store_true", help="Write the active step 4b levels file")
    args = parser.parse_args()
    run(args.llm_levels, args.htm_dir, args.fetch, args.apply)


if __name__ == "__main__":
    main()
