"""Draw the graph as PNGs, read live from whichever database .env points at.

Four framings sharing one set of positions, so they read as the same graph.
Positions are placed by hand rather than force-directed, so reruns are identical
and the pictures can go in a document without shifting every time.

    python3 render.py          # writes images/*.png

Needs matplotlib on top of the base requirements.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

import toy_graph as tg

OUT_DIR = Path(__file__).parent / "images"

INK = "#1c1c1e"
MUTED = "#86868b"
PROPOSED = "#b8503f"

HW, HH = 0.60, 0.33      # box half-width and half-height, in data units
GAP = 0.07               # space between the box edge and the arrow tip

FILL = {
    "COMPANY": "#dbe7f6", "FILING": "#e4dcf2", "SECTION": "#e8e8ea",
    "SUBSIDIARY": "#d2eae6", "SEGMENT": "#f7e6cc", "FACT": "#d9ecd6",
}
STROKE = {
    "COMPANY": "#4a7ab5", "FILING": "#7a66ad", "SECTION": "#9a9aa0",
    "SUBSIDIARY": "#3f968a", "SEGMENT": "#c08a3e", "FACT": "#5b9455",
}

POS = {
    tg.SEC_ITEM7: (1.7, 7.2),
    tg.COASTAL: (0.0, 6.6),
    tg.TENK: (1.7, 5.9),
    tg.TENKA: (1.7, 4.5),
    tg.NORTHWIND: (0.0, 4.0),
    tg.SEG_FREIGHT: (3.8, 3.0),
    tg.SUB_HARBOR: (1.7, 2.0),
    tg.SEG_WAREHOUSE: (3.8, 0.8),
    tg.FACT_24_FILED: (6.1, 5.9),
    tg.FACT_24_REST: (6.1, 4.3),
    tg.FACT_23_FILED: (6.1, 2.5),
}

CURVE = {("REPORTS_SEGMENT", tg.SEG_WAREHOUSE): -0.14}

LABEL_T = {
    ("REPORTS_SEGMENT", tg.SEG_WAREHOUSE): 0.78,
    ("REPORTS", tg.FACT_23_FILED): 0.56,
    ("SCOPES", tg.FACT_24_REST): 0.74,
    ("SCOPES", tg.FACT_24_FILED): 0.66,
    ("FILES", tg.TENKA): 0.62,
}

LABEL_OFF = {
    ("CONSOLIDATED_INTO", tg.SEG_FREIGHT): -0.30,
    ("REPORTS_SEGMENT", tg.SEG_WAREHOUSE): -0.22,
    ("REPORTS", tg.FACT_23_FILED): 0.22,
    ("PEER_OF", tg.COASTAL): 0.0,
    ("AMENDS", tg.TENK): 0.0,
    ("CONTAINS", tg.SEC_ITEM7): 0.0,
}
LABEL_OFF_DEFAULT = 0.17

FRAMINGS = {
    "1-whole-graph": {
        "title": "Whole graph — 11 nodes, 15 relationships",
        "nodes": None,
    },
    "2-answer-path": {
        "title": "The answer path — two FY2024 figures, told apart only by AMENDS",
        "nodes": {tg.SUB_HARBOR, tg.SEG_FREIGHT, tg.FACT_24_FILED,
                  tg.FACT_24_REST, tg.TENK, tg.TENKA},
    },
    "3-hop1-ambiguous": {
        "title": "Subsidiary to segment, with the schema as it stands — two segments back",
        "nodes": {tg.SUB_HARBOR, tg.NORTHWIND, tg.SEG_FREIGHT, tg.SEG_WAREHOUSE},
        "drop_rels": {"CONSOLIDATED_INTO"},
    },
    "4-restatement": {
        "title": "The correction on its own",
        "nodes": {tg.TENK, tg.TENKA, tg.FACT_24_FILED, tg.FACT_24_REST, tg.SEG_FREIGHT},
    },
}

# Which numbers are out of date, judged the correct way: an amendment has to
# replace that same concept for that same period.
Q_SUPERSEDED = """
MATCH (f:FACT)<-[:REPORTS]-(filing:FILING)<-[:AMENDS]-(amender:FILING)
MATCH (amender)-[:REPORTS]->(replacement:FACT)
WHERE replacement.concept = f.concept AND replacement.period_end = f.period_end
RETURN collect(DISTINCT f.key) AS keys
"""


def fetch():
    """Read the graph back out of the database rather than from toy_graph.py."""
    with tg.driver() as drv, drv.session() as session:
        nodes = {
            r["key"]: {"label": r["label"], "display": r["display"]}
            for r in session.run(
                "MATCH (n) RETURN n.key AS key, labels(n)[0] AS label, n.display AS display"
            )
        }
        rels = [
            (r["src"], r["dst"], r["rel"], r["in_v1"])
            for r in session.run(
                "MATCH (a)-[r]->(b) RETURN a.key AS src, b.key AS dst, type(r) AS rel, "
                "coalesce(r.in_schema_v1, true) AS in_v1"
            )
        ]
        superseded = set(session.run(Q_SUPERSEDED).single()["keys"])
    return nodes, rels, superseded


def boundary(centre, towards):
    """Where the line to `towards` leaves `centre`'s box, plus a small gap."""
    cx, cy = centre
    dx, dy = towards[0] - cx, towards[1] - cy
    if dx == 0 and dy == 0:
        return centre
    scales = []
    if dx:
        scales.append(HW / abs(dx))
    if dy:
        scales.append(HH / abs(dy))
    s = min(scales)
    length = (dx ** 2 + dy ** 2) ** 0.5
    return cx + dx * s + dx / length * GAP, cy + dy * s + dy / length * GAP


def wrap(text, width=19):
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return "\n".join(lines)


LEGEND_TEXT = {
    "stale": "Dashed node = figure replaced by a later filing.",
    "proposed": "Dashed red edge = proposed, not in the schema yet.",
}


def draw(ax, nodes, rels, superseded, keep, drop_rels):
    """Draw one framing and return the legend lines it actually needs."""
    shown = {k: POS[k] for k in nodes if keep is None or k in keep}
    legend = []

    for node_key, (x, y) in shown.items():
        data = nodes[node_key]
        stale = node_key in superseded
        ax.add_patch(FancyBboxPatch(
            (x - HW, y - HH), HW * 2, HH * 2,
            boxstyle="round,pad=0.03,rounding_size=0.08",
            facecolor=FILL[data["label"]], edgecolor=STROKE[data["label"]],
            linewidth=1.3, linestyle=(0, (2, 2)) if stale else "-", zorder=3,
        ))
        ax.text(x, y, f"{data['label']}\n{wrap(data['display'])}",
                ha="center", va="center", fontsize=6.2,
                color=MUTED if stale else INK, zorder=4, linespacing=1.4)
        if stale:
            ax.text(x, y - HH - 0.16, "replaced", ha="center", va="center",
                    fontsize=5.4, color=MUTED, style="italic", zorder=4)
            if "stale" not in legend:
                legend.append("stale")

    for src, dst, rel, in_v1 in rels:
        if src not in shown or dst not in shown or rel in drop_rels:
            continue
        a, b = boundary(shown[src], shown[dst]), boundary(shown[dst], shown[src])
        rad = CURVE.get((rel, dst), 0.0)
        new = not in_v1
        if new and "proposed" not in legend:
            legend.append("proposed")

        ax.add_patch(FancyArrowPatch(
            a, b, connectionstyle=f"arc3,rad={rad}",
            arrowstyle="-|>", mutation_scale=10,
            linewidth=2.2 if new else 1.0,
            linestyle=(0, (3.5, 2)) if new else "-",
            color=PROPOSED if new else "#a6a6ab", zorder=2,
        ))

        # Sit the label beside the edge. On top of it, a short edge disappears
        # behind its own caption.
        t = LABEL_T.get((rel, dst), 0.5)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = (dx ** 2 + dy ** 2) ** 0.5 or 1.0
        off = LABEL_OFF.get((rel, dst), LABEL_OFF_DEFAULT)
        nx, ny = -dy / length, dx / length
        ax.text(a[0] + dx * t + nx * off,
                a[1] + dy * t + ny * off + rad * 1.2,
                f"{rel}\nnot in v1" if new else rel,
                ha="center", va="center", fontsize=5.4,
                color=PROPOSED if new else MUTED, zorder=5,
                bbox=dict(boxstyle="round,pad=0.14", facecolor="white",
                          edgecolor="none", alpha=0.86))

    return legend


def render(name, spec, nodes, rels, superseded):
    keep = spec["nodes"]
    fig, ax = plt.subplots(figsize=(10.0, 7.2), dpi=220)
    legend = draw(ax, nodes, rels, superseded, keep, spec.get("drop_rels", set()))

    pts = [POS[k] for k in (keep if keep else POS)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    ax.set_xlim(min(xs) - 1.0, max(xs) + 1.0)
    ax.set_ylim(min(ys) - 0.9, max(ys) + 0.9)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(spec["title"], fontsize=10, color=INK, pad=14, loc="left")
    if legend:
        ax.text(0.0, -0.03, "    ".join(LEGEND_TEXT[k] for k in legend),
                transform=ax.transAxes, fontsize=6.4, color=MUTED)

    path = OUT_DIR / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print(f"reading from {tg.URI}")
    nodes, rels, superseded = fetch()

    missing = [k for k in nodes if k not in POS]
    if missing:
        print(f"  {len(missing)} nodes have no position and will be skipped.")
        print("  Add them to POS after changing the schema.")

    print(f"  {len(nodes)} nodes, {len(rels)} relationships, "
          f"{len(superseded)} replaced")
    for name, spec in FRAMINGS.items():
        path = render(name, spec, nodes, rels, superseded)
        print(f"  wrote {path.relative_to(Path(__file__).parent)}")


if __name__ == "__main__":
    main()
