"""Lists: bullets and lettered items, where a list ends, and whether it becomes one list_group.

A list starts after an intro ending in ":" (bullets, or lettered / numbered items), after a
heading (the heading is the intro), or after a sentence announcing "the following". It is merged
into one list_group unit only when the items cannot stand alone and the group stays short:
  - short names: every item is a fragment and items average under short_name_max_chars
    (product or factor names), or
  - at least one item is a fragment and the whole group (last intro sentence plus all items) is
    at most list_merge_max_chars.
Every other list is split: each item is labelled on its own, and its units carry list_intro_id,
the unit_id of the intro's last sentence (or of the heading). Split list items are never dropped
as too_short: their meaning comes from the intro.
"""

from __future__ import annotations

import re

from ..clean import ITEM_MARKER, has_table_cells
from .sentences import TERMINAL

# A bullet, with the "|" of a bullet exported as its own table cell after it ("• | Revenue grew").
# "" is the Symbol-font bullet (NetApp).
BULLET = re.compile(r"^(?:[•●◦▪‣]\s*(?:\|\s*)?|[–-]\s+(?:\|\s*)?|o\s+)")
# The last sentence of an intro that announces a list without a ":"
# (McKesson: "The following summary provides highlights ... for the year ended March 31, 2021.").
ANNOUNCES_LIST = re.compile(r"\bthe following\b|\bas follows\b", re.IGNORECASE)
# A list item that ends so that another item must follow: "...;", "...; and", "...,".
ITEM_GOES_ON = re.compile(r"(?:[;,]|;\s+(?:and|or)|,\s+(?:and|or)|\band/or)$")


def is_bullet(text: str) -> bool:
    return bool(BULLET.match(text))


def item_marker(text: str) -> re.Match | None:
    """The bullet or letter/number that opens a list item, or None."""
    return BULLET.match(text) or ITEM_MARKER.match(text)


def list_items_end(paragraphs: list[dict], i: int, lettered: bool) -> int:
    """Index after the last list item of the run starting at paragraph i.

    An item is a bullet paragraph, or, in a lettered run ("a.", "(1)"), a lettered paragraph.
    A paragraph without a bullet also counts as an item when it is not a table row and either
    the item before it ends so that another must follow ("...;", "...; and", "...,") and it starts
    in lower case, or it is a short name between two short-name items (Parker-Hannifin's market
    lists: "• Power generation" / "Renewable energy" / "• Water purification"): the Data team
    export drops the bullet sign of some items (S12_bullet_lost).
    """
    j = i
    while j < len(paragraphs):
        text = paragraphs[j]["text"]
        if is_bullet(text) or (lettered and ITEM_MARKER.match(text)):
            j += 1
        elif j > i and not has_table_cells(text) and (
                (text[:1].islower() and ITEM_GOES_ON.search(paragraphs[j - 1]["text"].rstrip()))
                or (short_item(text) and short_item(paragraphs[j - 1]["text"])
                    and j + 1 < len(paragraphs) and is_bullet(paragraphs[j + 1]["text"]))):
            j += 1
        else:
            break
    return j


def short_item(text: str) -> bool:
    """A name in a list of names: at most 5 words, no end punctuation ("• Power generation",
    "Renewable energy"). Between two bullets, such a paragraph is an item whose bullet was lost."""
    body = BULLET.sub("", text)
    return 0 < len(body.split()) <= 5 and not TERMINAL.search(body) and not body.endswith(":")


# A label row of a names table: "Engineered Materials Group: | • Aerospace" (PH Item 1).
GROUP_ROW = re.compile(r"^([^|:]{2,60}):\s*\|\s*(.+)$")


def group_name(cell: str) -> bool:
    """A cell holding short names: letters and no digits, so table rows of amounts
    ("Products | $ | 36,512") are never names. Several names in one cell split on "•"
    ("• Microelectronics • Oil & gas • Refining"); "&" and a "(...)" abbreviation are not words
    ("Heating, ventilation & air conditioning (HVAC)")."""
    names = [n for n in re.split(r"\s*•\s*", cell) if n.strip()]
    return bool(names) and all(short_item(re.sub(r"\([^()]*\)|\s&\s", " ", n)) for n in names) \
        and bool(re.search(r"[A-Za-z]", cell)) and not re.search(r"\d", cell)


def group_label(text: str) -> bool:
    """A label ending in ":" and a cell holding one short name after it."""
    m = GROUP_ROW.match(text)
    return bool(m) and group_name(m.group(2))


def group_items_end(paragraphs: list[dict], i: int) -> int:
    """Index after the last row of short names from paragraph i: each row one or more short
    names (bulleted or not) joined by "|"; the next label row ends the group."""
    j = i
    while j < len(paragraphs):
        text = paragraphs[j]["text"]
        cells = [c.strip() for c in text.split("|")]
        if group_label(text) or not all(group_name(c) for c in cells):
            break
        j += 1
    return j


def starts_list(paragraphs: list[dict], i: int, colon_intro: bool) -> bool:
    """True when paragraph i opens a list: a bullet, or after a ":" intro a lettered item."""
    if i >= len(paragraphs):
        return False
    text = paragraphs[i]["text"]
    return is_bullet(text) or (colon_intro and bool(ITEM_MARKER.match(text)))


def is_fragment(text: str) -> bool:
    """A bullet item that cannot stand alone ("Driving customer adoption...", "tariffs;", "iPad Air")."""
    item = ITEM_MARKER.sub("", BULLET.sub("", text))
    first = item.split()[0] if item.split() else ""
    return (item[:1].islower() or first.endswith("ing") or item.rstrip().endswith((";", ",", "; and", "; or"))
            or not TERMINAL.search(item))


def merge_list(intro_sentence: str, bullets: list[dict], cfg: dict) -> bool:
    """True when an intro and its bullets should be one list_group unit (see the module docstring).

    Bullets that are all complete sentences ("Microsoft Cloud revenue increased 29%.") are never
    merged; nor is a list too long to read as one statement (Micron's 17 international risks).
    """
    texts = [b["text"] for b in bullets]
    fragments = sum(is_fragment(t) for t in texts)
    if fragments == len(texts) and sum(map(len, texts)) / len(texts) < cfg.get("short_name_max_chars", 60):
        return True
    group_chars = len(" ".join([intro_sentence] + texts))
    return fragments > 0 and group_chars <= cfg.get("list_merge_max_chars", 600)
