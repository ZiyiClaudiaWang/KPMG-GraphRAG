"""Build the KPMG update deck as a .pptx for importing into Google Slides.

A local .pptx cannot edit the live Slides deck. Import it:
Google Slides -> File -> Import slides -> Upload -> pick all -> Import.

    python3 tools/make_deck_figures.py --captures /tmp/figs --out /tmp/figs
    python3 tools/build_deck.py --figures /tmp/figs --out ~/Downloads/deck.pptx

Slide size matches the existing KPMG deck, 10 x 5.625in, so imported slides
do not get letterboxed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

W, H = Inches(10), Inches(5.625)
INK = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x6B, 0x72, 0x80)
GREEN = RGBColor(0x2F, 0x6F, 0x4F)
BLUE = RGBColor(0x1F, 0x4E, 0x79)
WARN = RGBColor(0xA3, 0x3A, 0x2A)
FONT = "Helvetica Neue"


def textbox(slide, x, y, w, h, runs, size=12, align=PP_ALIGN.LEFT, spacing=1.18):
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.word_wrap = True
    for i, item in enumerate(runs):
        text, colour, bold, isize = (item + (None,) * 4)[:4]
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.alignment = align
        para.line_spacing = spacing
        run = para.add_run()
        run.text = text
        run.font.size = Pt(isize or size)
        run.font.bold = bool(bold)
        run.font.color.rgb = colour or INK
        run.font.name = FONT
    return box


def picture(slide, path, x, y, max_w, max_h):
    """Place a figure, scaled to fit inside the box and centred in it."""
    from PIL import Image
    iw, ih = Image.open(path).size
    scale = min(max_w / iw, max_h / ih)
    w, h = int(iw * scale), int(ih * scale)
    return slide.shapes.add_picture(str(path), int(x + (max_w - w) / 2),
                                    int(y + (max_h - h) / 2), width=w, height=h)


def new_slide(prs, title, standfirst=None):
    slide = prs.slides.add_slide(prs.slide_layouts[6])   # blank
    textbox(slide, Inches(0.45), Inches(0.28), Inches(9.1), Inches(0.5),
            [(title, INK, True, 21)])
    if standfirst:
        textbox(slide, Inches(0.45), Inches(0.78), Inches(9.1), Inches(0.4),
                [(standfirst, MUTED, False, 11)])
    return slide


def build(figures: Path, out: Path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    # 1 — last week vs this week
    s = new_slide(prs, "Last week both tools raised. This week they answer.",
                  "29 Oct, our own slide: “both tools raise, every call — "
                  "no backend is registered on main.”")
    rows = [
        ("Graph arm", "raises on every call", "retrieves — full-text over Claims, 3-hop walk"),
        ("Structured arm", "ignores the question", "reads it — 0/10 → 10/10 on the gold set"),
        ("Corpus", "1 filing, Microsoft", "20 filings, 10 companies, 21,505 facts"),
        ("Entity resolution", "measured on a branch", "merged — main reports recall 0.789"),
        ("CI", "none", "4 jobs, first coverage the project has had"),
    ]
    table = s.shapes.add_table(len(rows) + 1, 3, Inches(0.45), Inches(1.3),
                               Inches(9.1), Inches(2.9)).table
    table.columns[0].width = Inches(2.0)
    table.columns[1].width = Inches(2.9)
    table.columns[2].width = Inches(4.2)
    def cell_text(cell, text, *, bold, size, colour):
        """Set a cell's text. An empty string leaves a paragraph with no runs,
        so add the run rather than assigning cell.text."""
        para = cell.text_frame.paragraphs[0]
        run = para.add_run()
        run.text = text
        run.font.bold = bold
        run.font.size = Pt(size)
        run.font.name = FONT
        run.font.color.rgb = colour

    for c, head in enumerate([" ", "29 Oct", "5 Nov"]):
        cell_text(table.cell(0, c), head, bold=True, size=11,
                  colour=BLUE if c == 2 else MUTED)
    for r, row in enumerate(rows, start=1):
        for c, text in enumerate(row):
            cell_text(table.cell(r, c), text, bold=(c == 0), size=10,
                      colour=GREEN if c == 2 else (INK if c == 0 else MUTED))
    textbox(s, Inches(0.45), Inches(4.42), Inches(9.1), Inches(0.9),
            [("Say out loud: all three retrieval changes are in review, not merged "
              "(#57, #58, #59). The structured arm runs on all twenty filings today. "
              "The graph arm is built and tested but has no data loaded.", WARN, False, 10)])

    # 2 — the corpus
    s = new_slide(prs, "The structured arm, across ten companies",
                  "Built from EDGAR, about two seconds a filing. "
                  "AAPL CASY MCK MDT MSFT MU NKE NTAP PH TTWO.")
    picture(s, figures / "fig_build.png", Inches(0.45), Inches(1.18),
            Inches(5.6), Inches(4.0))
    picture(s, figures / "fig_arm.png", Inches(6.2), Inches(1.3),
            Inches(3.35), Inches(1.5))
    textbox(s, Inches(6.2), Inches(3.0), Inches(3.35), Inches(2.2), [
        ("Ask about the company, get the company.", INK, True, 10),
        ("Name a segment, get the segment. Same XBRL "
         "concept — only a dimension tells them apart.", MUTED, False, 9.5),
        ("", INK, False, 6),
        ("Nike consolidated   11.2bn", GREEN, True, 9.5),
        ("Converse segment    22m", BLUE, True, 9.5),
    ])

    # 3 — identity
    s = new_slide(prs, "How a number gets its identity, and how the arms line up",
                  "Three arms produce three kinds of evidence. One rule keeps a "
                  "citation unambiguous.")
    picture(s, figures / "fig_ids.png", Inches(0.3), Inches(1.15),
            Inches(9.4), Inches(4.3))

    # 4 — measured
    s = new_slide(prs, "Measured, not asserted",
                  "The gold questions record which concept answers each one, so "
                  "they are an answer key — not our own marking.")
    picture(s, figures / "fig_gold.png", Inches(0.45), Inches(1.15),
            Inches(5.9), Inches(4.0))
    textbox(s, Inches(6.55), Inches(1.3), Inches(3.0), Inches(4.0), [
        ("Found by running it", INK, True, 12),
        ("", INK, False, 5),
        ("The loader could not load the corpus.", INK, True, 9.5),
        ("Any filing with no conflicting facts crashed it.", MUTED, False, 9),
        ("", INK, False, 4),
        ("Then 9 of 20 failed the other way.", INK, True, 9.5),
        ("An empty first filing set every column's type, "
         "and the sixth could not be inserted.", MUTED, False, 9),
        ("", INK, False, 4),
        ("Nike's revenue came back 50x too small.", INK, True, 9.5),
        ("A segment slice tied with the consolidated figure.", MUTED, False, 9),
        ("", INK, False, 4),
        ("A whole arm had never been tested.", INK, True, 9.5),
        ("A missing CI dependency meant nothing in "
         "structured/ ran. CI passed throughout.", MUTED, False, 9),
    ])

    # 5 — next
    s = new_slide(prs, "The demo question, and what lands by Monday",
                  "M18, from Eval's own list — so we are not grading our arm on a "
                  "benchmark we wrote.")
    textbox(s, Inches(0.45), Inches(1.2), Inches(9.1), Inches(0.5),
            [("“Which risk factor do Microsoft and Apple both disclose, "
              "and what sentence does each use?”", BLUE, True, 14)])
    textbox(s, Inches(0.45), Inches(1.85), Inches(9.1), Inches(0.9), [
        ("It only became answerable this week. Risk factors used to be per-company "
         "hashes, so there was no shared node for two companies to meet at. They are "
         "now a shared ten-theme library — exactly the mechanism the question needs.",
         MUTED, False, 10)])
    textbox(s, Inches(0.45), Inches(2.95), Inches(4.4), Inches(2.2), [
        ("By tomorrow midday", INK, True, 12),
        ("Three PRs reviewed and merged", INK, False, 10),
        ("Graph loaded from the 20-filing extraction", INK, False, 10),
        ("One question through both arms, with citations", INK, False, 10),
    ])
    textbox(s, Inches(5.15), Inches(2.95), Inches(4.4), Inches(2.2), [
        ("By Monday — Eval round 1 scores main", INK, True, 12),
        ("Canonical source: Data's Postgres, our rules", INK, False, 10),
        ("Canonical fact_id, and which key is cited (#56)", INK, False, 10),
        ("", INK, False, 5),
        ("The demo will not route between arms — that is "
         "the Agents team's card.", WARN, False, 9.5),
    ])

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figures", default="/tmp/figs")
    ap.add_argument("--out", default=str(Path.home() / "Downloads" /
                                         "KPMG GraphRAG update 5 Nov.pptx"))
    args = ap.parse_args()
    print(build(Path(args.figures), Path(args.out)))


if __name__ == "__main__":
    main()
