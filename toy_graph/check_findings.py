"""Run the toy question against the graph and show where schema v1 falls short.

Q. Which segment absorbed Harbor Logistics, how did its FY2024 revenue move, and
   is the figure we would quote still the one the company stands behind?

Numeric values come from a local fixture lookup, not the graph or a live fact store.
Each finding is shown as a pair: the query you would naturally write, and the
corrected one. The difference between them is the point.
"""

import toy_graph as tg

# ---- Finding 1 ------------------------------------------------------------
# Settled v1 has no edge from a SUBSIDIARY to the SEGMENT that consolidates it.
# The only route is up to the parent and back down, which returns every segment.

Q1_V1 = """
MATCH (s:Subsidiary {key: $sub})<-[:OWNS]-(:Company)-[:HAS_SEGMENT]->(seg:Segment)
RETURN seg.key AS key, seg.name AS name ORDER BY name
"""

Q1_PROPOSED = """
MATCH (s:Subsidiary {key: $sub})-[r:CONSOLIDATED_INTO]->(seg:Segment)
RETURN seg.key AS key, seg.name AS name, r.quote AS quote,
       r.prov_source_chunk_id AS chunk, r.prov_section_id AS section_id,
       r.prov_accession AS accession_number,
       r.prov_char_start AS start, r.prov_char_end AS end
"""

# ---- Finding 2 ------------------------------------------------------------
# AMENDS is filing-level. Restatement is fact-level. Judging supersession by
# "was the filing amended" marks every fact in the original stale, including
# periods the amendment never touched.

Q2_BY_FILING = """
MATCH (seg:Segment {key: $seg})-[:SCOPES]->(f:Fact)
WHERE f.concept = $concept AND f.period_end = $period_end
MATCH (filing:Filing)-[:REPORTS]->(f)
OPTIONAL MATCH (amender:Filing)-[:AMENDS]->(filing)
RETURN f.key AS fact_key, f.basis AS basis, filing.form AS filed_in,
       amender.form AS superseded_by
ORDER BY superseded_by IS NULL DESC
"""

Q2_BY_FACT = """
MATCH (seg:Segment {key: $seg})-[:SCOPES]->(f:Fact)
WHERE f.concept = $concept AND f.period_end = $period_end
MATCH (filing:Filing)-[:REPORTS]->(f)
RETURN f.key AS fact_key, f.basis AS basis, filing.form AS filed_in,
       CASE WHEN f.key IN $superseded_keys THEN '10-K/A' ELSE null END AS superseded_by
ORDER BY superseded_by IS NULL DESC
"""


def validated_superseded_keys():
    """Explicit replacement evidence in the constructed fixture only.

    Matching metadata is a consistency check, not proof of replacement.
    The explicit mapping must come from Data/evidence in real ingestion.
    """
    facts = {nid: p for nid, label, p in tg.NODES if label == "Fact"}
    for replacement, original in tg.FACT_REPLACEMENTS.items():
        for field in ("entity_cik", "concept", "period_start", "period_end", "dimensions", "unit"):
            expect(facts[replacement][field] == facts[original][field],
                   f"Replacement scope mismatch: {field}")
    return list(tg.FACT_REPLACEMENTS.values())


def show(rows):
    for r in rows:
        state = f"superseded by {r['superseded_by']}" if r["superseded_by"] else "CURRENT"
        print(f"    ${r['value']:>13,.0f}  {r['basis']:<12} in {r['filed_in']:<7} — {state}")


class CheckFailed(AssertionError):
    """A finding stopped reproducing, or started reproducing differently."""


def expect(condition, message):
    """Fail loudly. A Cypher MATCH that finds nothing returns no rows and no
    error, so every query here states what it expects to get back."""
    if not condition:
        raise CheckFailed(message)


def current(rows, period):
    """The one row not superseded. Two would mean the graph cannot say which
    figure to quote, which is the bug this file exists to catch."""
    live = [r for r in rows if not r["superseded_by"]]
    expect(live, f"{period}: no current value — every row came back superseded")
    expect(len(live) == 1, f"{period}: {len(live)} current values, expected exactly 1")
    return live[0]


def main():
    with tg.driver() as drv, drv.session() as session:
