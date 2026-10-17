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
    "Company": "#dbe7f6", "Filing": "#e4dcf2", "Section": "#e8e8ea",
    "Subsidiary": "#d2eae6", "Segment": "#f7e6cc", "Fact": "#d9ecd6",
}
STROKE = {
    "Company": "#4a7ab5", "Filing": "#7a66ad", "Section": "#9a9aa0",
    "Subsidiary": "#3f968a", "Segment": "#c08a3e", "Fact": "#5b9455",
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

CURVE = {("HAS_SEGMENT", tg.SEG_WAREHOUSE): -0.14}

LABEL_T = {
    ("HAS_SEGMENT", tg.SEG_WAREHOUSE): 0.78,
    ("REPORTS", tg.FACT_23_FILED): 0.56,
    ("SCOPES", tg.FACT_24_REST): 0.74,
    ("SCOPES", tg.FACT_24_FILED): 0.66,
    ("FILES", tg.TENKA): 0.62,
}

LABEL_OFF = {
    ("CONSOLIDATED_INTO", tg.SEG_FREIGHT): -0.30,
    ("HAS_SEGMENT", tg.SEG_WAREHOUSE): -0.22,
    ("REPORTS", tg.FACT_23_FILED): 0.22,
    ("PEER_OF", tg.COASTAL): 0.0,
    ("AMENDS", tg.TENK): 0.0,
    ("CONTAINS", tg.SEC_ITEM7): 0.0,
}
LABEL_OFF_DEFAULT = 0.17

FRAMINGS = {
    "1-whole-graph": {
        "title": "CW draft schema probe — constructed findings plus Apple claims",
        "nodes": None,
    },
    "2-answer-path": {
        "title": "The answer path — two FY2024 figures, requiring explicit fact replacement evidence",
        "nodes": {tg.SUB_HARBOR, tg.SEG_FREIGHT, tg.FACT_24_FILED,
                  tg.FACT_24_REST, tg.TENK, tg.TENKA},
    },
    "3-hop1-ambiguous": {
        "title": "Subsidiary to segment, historical parent-only traversal — two segments back",
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
