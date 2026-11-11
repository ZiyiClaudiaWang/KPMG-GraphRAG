"""Fill the `hops` half of Eval's hops_v2.csv from the gold set.

Eval filled `filings` in code and left `hops`, `graph_path`, `graph_support`
and `graph_note` to us. This derives them the same way, so the values can be
re-run when questions change instead of being hand-typed once and going stale.

    python3 tools/fill_hops.py \
        --gold   "~/Projects/kpmg-eval/Week 8/data/benchmark_v2.jsonl" \
        --hops   "~/Projects/kpmg-eval/Week 8/data/hops_v2.csv" \
        --output "~/Projects/kpmg-eval/Week 8/data/hops_v2.csv"

What `hops` counts: edges walked on **our** ontology, from the entity the
question names to the evidence that answers it. That is not the same count as
the gold set's own `graph.paths`, and the difference is the point. Their model
reaches a fact in two edges, Company -FILED-> Filing -REPORTS-> Fact. Ours
reaches a claim in three, because a Section sits between the filing and the
text, and it reaches no facts at all — XBRL lives in the structured arm, not
the graph.

So a question routed to `xbrl` gets no hop count. Saying "2" would imply the
graph could walk it.

Nothing here guesses. A question whose path we cannot derive is written as
`needs-review` with the reason, because a filled-in number nobody checked is
worse than a blank one.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

# Our ontology, and what each leg costs.
#
#   (Company)-[:FILES]->(Filing)-[:CONTAINS]->(Section)-[:SUPPORTS]->(Claim)
#
# Three edges to reach a claim from the company the question names. Every
# prose answer starts from that and adds to it.
CLAIM_PATH = "Company -FILES-> Filing -CONTAINS-> Section -SUPPORTS-> Claim"
CLAIM_HOPS = 3

# A risk theme is one edge past the claim, and an intersection walks back down
# the other side: claim, theme, claim.
RISK_PATH = CLAIM_PATH + " -CONCERNS_RISK-> RiskFactor"
RISK_HOPS = CLAIM_HOPS + 1

# Exhibit 21 lands as one edge, Company -OWNS-> Subsidiary. Jurisdiction is a
# property on the subsidiary (country, state_or_region), not a node, so a
# question that joins two companies on a shared jurisdiction walks two edges
# and compares a property. The gold set's path for those assumes a Jurisdiction
# node, which we do not have.
OWNS_PATH = "Company -OWNS-> Subsidiary"
OWNS_HOPS = 1

STRUCTURED_NOTE = "XBRL facts are not in the graph; the structured arm answers this"
EX21_NOTE = "needs the Exhibit 21 pipeline merged and loaded"
JURISDICTION_NOTE = (
    "gold path assumes a Jurisdiction node; ours is a property on Subsidiary "
    "(country, state_or_region), so this is two OWNS edges and a property join"
)


HYBRID_NOTE = (
    "hybrid: hops counts the graph's leg to the prose only; the figure comes "
    "from the structured arm, which the graph cannot reach"
)

# Verdicts already given in the week 8 reply, so the script does not ask for
# them a second time. Keyed by question, with the reason we gave at the time.
SETTLED = {
    "EV1R-003": {
        "hops": str(CLAIM_HOPS),
        "graph_path": CLAIM_PATH,
        "graph_support": "yes",
        "graph_note": (
            "we hold the MD&A sentences but do not model Event as a node, so "
            "this resolves by text rather than traversal"
        ),
    },
    "EV1R-006": {
        "hops": str(OWNS_HOPS),
        "graph_path": OWNS_PATH + " -> product line -> segment",
        "graph_support": "blocked",
        "graph_note": EX21_NOTE + "; the segment attribution is not available either",
    },
}


def classify(row: dict) -> dict:
    """What a question costs on our ontology, or why we cannot say."""
    route = row.get("route")
    graph = row.get("graph") or {}
    pattern = graph.get("pattern")

    settled = SETTLED.get(row["qid"])
    if settled:
        return {**settled, "filled_by": "graphrag:week8-reply"}

    # Half the answer is prose and half is a number. The graph walks to the
    # prose and stops, so count that leg and say where the figure comes from.
    # Leaving it blank would read as "the graph cannot help here" when it
    # supplies one side of the answer.
    if route == "xbrl+text":
        return {
            "hops": str(CLAIM_HOPS),
            "graph_path": CLAIM_PATH,
            "graph_support": "partial",
            "graph_note": HYBRID_NOTE,
            "filled_by": "graphrag:derived",
        }

    # Numbers. The graph holds no facts, so there is no path to count.
    if route in ("xbrl", "metadata"):
        return {
            "hops": "",
            "graph_path": "",
            "graph_support": "no",
            "graph_note": STRUCTURED_NOTE,
            "filled_by": "graphrag:derived",
        }

    # The unanswerable controls. Returning nothing is the right answer, and
    # that is a property of the graph rather than a path through it.
    if route == "refusal":
        support = "yes" if pattern == "absent_edge" else "n/a"
        note = (
            "the absence is real in our graph, which is what the control tests"
            if pattern == "absent_edge"
            else "unanswerable; no path expected"
        )
        return {
            "hops": "",
            "graph_path": "",
            "graph_support": support,
            "graph_note": note,
            "filled_by": "graphrag:derived",
        }

    # Exhibit 21. The edge ships in the subsidiary pipeline but is not loaded,
    # so the path is known and the support is not there yet.
    if pattern == "relationship":
        joins_two_companies = "<-" in (graph.get("path") or "")
        return {
            "hops": str(OWNS_HOPS * 2 if joins_two_companies else OWNS_HOPS),
            "graph_path": OWNS_PATH + (" x2, joined on country" if joins_two_companies else ""),
            "graph_support": "blocked",
            "graph_note": EX21_NOTE + ("; " + JURISDICTION_NOTE if joins_two_companies else ""),
            "filled_by": "graphrag:derived",
        }

    # Prose. Everything the graph actually serves today walks the claim path.
    if route == "text":
        if pattern in (None, "list_aggregate", "cross_period", "text_plus_fact"):
            return {
                "hops": str(CLAIM_HOPS),
                "graph_path": CLAIM_PATH,
                "graph_support": "yes",
                "graph_note": "",
                "filled_by": "graphrag:derived",
            }
        # multi_hop and event_effect are the ones worth a person's eye: the
        # question names an entity the claim text does not, and whether our
        # path reaches it is a judgement rather than a count.
        return {
            "hops": "",
            "graph_path": CLAIM_PATH + " (+ ?)",
            "graph_support": "review",
            "graph_note": f"pattern '{pattern}' may need more than the claim path",
            "filled_by": "graphrag:needs-review",
        }

    return {
        "hops": "",
        "graph_path": "",
        "graph_support": "review",
        "graph_note": f"route '{route}' pattern '{pattern}' not mapped",
        "filled_by": "graphrag:needs-review",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gold", required=True, help="benchmark_v2.jsonl")
    p.add_argument("--hops", required=True, help="hops_v2.csv to fill")
    p.add_argument("--output", required=True)
    p.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = p.parse_args()

    gold = {}
    with open(Path(args.gold).expanduser()) as fh:
        for line in fh:
            row = json.loads(line)
            gold[row["qid"]] = row

    with open(Path(args.hops).expanduser()) as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        rows = list(reader)

    counts = {"filled": 0, "needs-review": 0, "missing-from-gold": 0}
    support = {}
    for row in rows:
        source = gold.get(row["qid"])
        if source is None:
            row["graph_note"] = "no matching question in the gold set"
            row["filled_by"] = "graphrag:needs-review"
            counts["missing-from-gold"] += 1
            continue
        filled = classify(source)
        row.update(filled)
        bucket = "needs-review" if filled["filled_by"].endswith("needs-review") else "filled"
        counts[bucket] += 1
        support[filled["graph_support"]] = support.get(filled["graph_support"], 0) + 1

    print(f"{len(rows)} rows")
    for key, n in counts.items():
        print(f"  {key:<20} {n}")
    print("  graph_support:", ", ".join(f"{k}={v}" for k, v in sorted(support.items())))

    if args.dry_run:
        print("\n--dry-run, nothing written")
        return

    out = Path(args.output).expanduser()
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
