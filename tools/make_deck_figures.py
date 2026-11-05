"""Render the terminal captures and the ID diagram as slide figures.

Everything on the slides came from running something. These are pictures of
that output rather than retyped summaries, so a reviewer asking "is that
real" can be shown the command beside it.

    python3 tools/make_deck_figures.py --captures /tmp/figs --out /tmp/figs
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

INK = "#1a1a1a"
MUTED = "#6b7280"
PAPER = "#ffffff"
SHELL = "#fbfbfa"
RULE = "#d9d6d0"
GRAPH = "#2f6f4f"
STRUCT = "#1f4e79"
RAG = "#8a6d3b"
WARN = "#a33a2a"


def terminal(text, out, width=12.0, fontsize=9.0, highlight=()):
    """A terminal capture, set in monospace on a pale panel."""
    lines = text.rstrip("\n").split("\n")
    height = 0.26 * len(lines) + 0.5
    fig = plt.figure(figsize=(width, height), dpi=220)
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.add_patch(FancyBboxPatch(
        (0.004, 0.01), 0.992, 0.98, boxstyle="round,pad=0.004,rounding_size=0.01",
        linewidth=0.8, edgecolor=RULE, facecolor=SHELL, transform=ax.transAxes))

    step = 1.0 / (len(lines) + 1.6)
    for i, line in enumerate(lines):
        colour = INK
        weight = "normal"
        if line.startswith("$"):
            colour, weight = MUTED, "bold"
        for needle, hue in highlight:
            if needle in line:
                colour, weight = hue, "bold"
        ax.text(0.018, 1 - (i + 1.1) * step, line, family="DejaVu Sans Mono",
                fontsize=fontsize, color=colour, weight=weight,
                va="center", ha="left", transform=ax.transAxes)
    fig.savefig(out, facecolor=PAPER)
    plt.close(fig)
    return out


def id_diagram(out):
    """Where each arm's identifier comes from, and what the three share."""
    fig = plt.figure(figsize=(12.0, 5.4), dpi=220)
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.4)
    ax.set_axis_off()

    def box(x, y, w, h, title, lines, colour):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.06,rounding_size=0.08",
            linewidth=1.3, edgecolor=colour, facecolor="white"))
        ax.text(x + 0.18, y + h - 0.3, title, fontsize=11, weight="bold", color=colour)
        for i, line in enumerate(lines):
            ax.text(x + 0.18, y + h - 0.62 - i * 0.29, line,
                    fontsize=8.6, color=INK, family="DejaVu Sans Mono")

    ax.text(0.3, 5.05, "Three arms, three identifiers, one rule",
            fontsize=15, weight="bold", color=INK)
    ax.text(0.3, 4.72, "The contract allows exactly one provenance key per result, "
                       "so a citation is never ambiguous about what it points at.",
            fontsize=9.6, color=MUTED)

    box(0.3, 2.75, 3.6, 1.72, "RAG — vector chunks", [
        "chunk_id", "", "from Data's chunking", "cites: a passage"], RAG)
    box(4.2, 2.75, 3.6, 1.72, "Graph arm — Claims", [
        "section_id", "", "adopted whole from Data", "cites: a sentence in a section"], GRAPH)
    box(8.1, 2.75, 3.6, 1.72, "Structured arm — XBRL", [
        "metadata.fact_id", "", "SHA-256 of 8 fields", "cites: one tagged number"], STRUCT)

    ax.add_patch(FancyBboxPatch(
        (0.3, 1.5), 11.4, 1.0, boxstyle="round,pad=0.06,rounding_size=0.08",
        linewidth=1.3, edgecolor=INK, facecolor="#f7f7f5"))
    ax.text(0.5, 2.22, "fact_id  =  sha256( cik | accession | concept | period_start | "
                       "period_end | period_type | dimensions | unit )",
            fontsize=9.6, family="DejaVu Sans Mono", color=INK)
    ax.text(0.5, 1.88, "The value is deliberately excluded. A restated figure in a 10-K/A "
                       "keeps the same fact_id as the original,",
            fontsize=9.2, color=MUTED)
    ax.text(0.5, 1.64, "so the two are comparable instead of looking like unrelated facts.",
            fontsize=9.2, color=MUTED)

    # Only the structured arm. The formula below is how fact_id is built, not
    # something the other two arms use, and an arrow from each would say they
    # all share it.
    ax.add_patch(FancyArrowPatch((9.9, 2.75), (9.9, 2.52), arrowstyle="-|>",
                                 mutation_scale=11, linewidth=1.0, color=STRUCT))

    ax.text(0.3, 1.12, "What aligns them", fontsize=10.5, weight="bold", color=INK)
    ax.text(0.3, 0.84, "accession_number  — every arm carries it, so three answers "
                       "resolve to one filing", fontsize=9.2, color=INK)
    ax.text(0.3, 0.58, "trace_id  — stamped on every row by the shared wrapper, so Eval "
                       "joins a result back to the question that caused it",
            fontsize=9.2, color=INK)
    ax.text(0.3, 0.26, "Still open: facts carry no section pointer, so the graph cites a "
                       "section and the structured arm cites a filing  (#40, #56)",
            fontsize=9.2, color=WARN)

    fig.savefig(out, facecolor=PAPER)
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", default="/tmp/figs")
    ap.add_argument("--out", default="/tmp/figs")
    args = ap.parse_args()
    src, dst = Path(args.captures), Path(args.out)
    dst.mkdir(parents=True, exist_ok=True)

    terminal((src / "build.txt").read_text(), dst / "fig_build.png", fontsize=8.0,
             highlight=[("20 loaded, 0 failed", GRAPH),
                        ("corpus_version", STRUCT),
                        ("23506 raw", INK)])
    terminal((src / "gold.txt").read_text(), dst / "fig_gold.png", fontsize=8.6,
             highlight=[("top-1 10/10", GRAPH)])
    terminal((src / "arm.txt").read_text(), dst / "fig_arm.png", fontsize=9.4,
             highlight=[("consolidated", GRAPH), ("OperatingSegmentsMember", STRUCT)])
    id_diagram(dst / "fig_ids.png")
    print("\n".join(str(p) for p in sorted(dst.glob("fig_*.png"))))


if __name__ == "__main__":
    main()
