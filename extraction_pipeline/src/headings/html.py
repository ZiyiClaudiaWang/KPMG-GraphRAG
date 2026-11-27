"""Read an EDGAR filing's HTML as a list of blocks with their typography, and align the blocks to
the Data-team export text. Library code: no pipeline paths or settings here.

A block is one paragraph-like element (p, div, li, a table row, ...). Each keeps its runs of
text with bold / italic / underline / size / capitals, its alignment and left indent, whether it
sits in a table, and whether its text is an internal link (an entry of a table of contents).
`align` gives each block its span in the export text, or leaves start = -1 when it is not found.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import re

from lxml import html

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
    caps: bool          # text-transform uppercase or small-caps
    link: bool = False  # inside <a href="#..."> (a link to another place in the filing)


@dataclass
class Block:
    idx: int            # position in the list html_blocks returns
    runs: list[Run] = field(default_factory=list)
    align: str = ""
    indent: float = 0.0
    in_table: bool = False
    table_id: int = -1  # in_table: which outermost <table> of the document (0, 1, ...); else -1
    page_break: bool = False
    start: int = -1     # span in the export text (raw coordinates), -1 = not found
    end: int = -1

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def is_link(self) -> bool:
        """All its text is an internal link: an entry of a table of contents."""
        runs = [r for r in self.runs if norm(r.text)]
        return bool(runs) and all(r.link for r in runs)


class Walker:
    def __init__(self, tree):
        self.rules = class_rules(tree)
        self.blocks: list[Block] = []
        self.cur: Block | None = None
        self.pending_break = False
        self.table_no = -1          # outermost <table> elements seen so far, minus one

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
        if tag == "a" and (el.get("href") or "").startswith("#"):
            st["_link"] = "1"
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
                  in_table=in_table, table_id=self.table_no if in_table else -1)
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
                             in_table=in_table, table_id=self.table_no if in_table else -1)
        caps = st.get("text-transform") == "uppercase" or st.get("font-variant") == "small-caps"
        self.cur.runs.append(Run(text, is_bold(st.get("font-weight")),
                                 st.get("font-style") == "italic",
                                 "underline" in st.get("text-decoration", ""),
                                 round(st.get("_size", 10.0), 1), caps, bool(st.get("_link"))))

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
            if not in_table:
                self.table_no += 1
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
