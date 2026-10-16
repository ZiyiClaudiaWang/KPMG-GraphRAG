"""Run the toy question against the graph and show where schema v1 falls short.

Q. Which segment absorbed Harbor Logistics, how did its FY2024 revenue move, and
   is the figure we would quote still the one the company stands behind?

Each finding is shown as a pair: the query you would naturally write, and the
corrected one. The difference between them is the point.
"""

import toy_graph as tg

# ---- Finding 1 ------------------------------------------------------------
# Settled v1 has no edge from a SUBSIDIARY to the SEGMENT that consolidates it.
# The only route is up to the parent and back down, which returns every segment.

Q1_V1 = """
MATCH (s:SUBSIDIARY {key: $sub})<-[:OWNS]-(:COMPANY)-[:REPORTS_SEGMENT]->(seg:SEGMENT)
RETURN seg.key AS key, seg.name AS name ORDER BY name
"""

Q1_PROPOSED = """
MATCH (s:SUBSIDIARY {key: $sub})-[r:CONSOLIDATED_INTO]->(seg:SEGMENT)
RETURN seg.key AS key, seg.name AS name, r.quote AS quote,
       r.prov_source_chunk_id AS chunk,
       r.prov_char_start AS start, r.prov_char_end AS end
"""

# ---- Finding 2 ------------------------------------------------------------
# AMENDS is filing-level. Restatement is fact-level. Judging supersession by
# "was the filing amended" marks every fact in the original stale, including
# periods the amendment never touched.

Q2_BY_FILING = """
MATCH (seg:SEGMENT {key: $seg})-[:SCOPES]->(f:FACT)
WHERE f.concept = $concept AND f.period_end = $period_end
MATCH (filing:FILING)-[:REPORTS]->(f)
OPTIONAL MATCH (amender:FILING)-[:AMENDS]->(filing)
RETURN f.value AS value, f.basis AS basis, filing.form_type AS filed_in,
       amender.form_type AS superseded_by
ORDER BY superseded_by IS NULL DESC
"""

Q2_BY_FACT = """
MATCH (seg:SEGMENT {key: $seg})-[:SCOPES]->(f:FACT)
WHERE f.concept = $concept AND f.period_end = $period_end
MATCH (filing:FILING)-[:REPORTS]->(f)
OPTIONAL MATCH (amender:FILING)-[:AMENDS]->(filing)
OPTIONAL MATCH (amender)-[:REPORTS]->(replacement:FACT)
  WHERE replacement.concept = f.concept AND replacement.period_end = f.period_end
RETURN f.key AS fact_key, f.value AS value, f.basis AS basis,
       filing.form_type AS filed_in,
       CASE WHEN replacement IS NULL THEN null ELSE amender.form_type END
         AS superseded_by
ORDER BY superseded_by IS NULL DESC
"""


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
        rows = lambda q, **p: [r.data() for r in session.run(q, **p)]

        print("FINDING 1 — subsidiary to segment\n")

        print("  schema v1 only:")
        v1 = rows(Q1_V1, sub=tg.SUB_HARBOR)
        for r in v1:
            print(f"    {r['name']}")
        print(f"  {len(v1)} rows. Ambiguous — the graph cannot say which segment "
              f"absorbed Harbor.\n")
        expect(len(v1) > 1,
               "finding 1 stopped reproducing: schema v1 returned "
               f"{len(v1)} segments, so the hop is no longer ambiguous. If an edge "
               "was added, move this check and update the schema doc.")

        print("  with the proposed CONSOLIDATED_INTO edge:")
        proposed = rows(Q1_PROPOSED, sub=tg.SUB_HARBOR)
        expect(len(proposed) == 1,
               f"the proposed edge returned {len(proposed)} segments, expected 1")
        hit = proposed[0]
        expect(hit["quote"], "the proposed edge carries no quote, so it cannot be cited")
        print(f"    {hit['name']}")
        print(f'    evidence: "{hit["quote"]}"')
        print(f"              {hit['chunk']} [{hit['start']}:{hit['end']}]\n")

        seg = hit["key"]

        print("FINDING 2 — what counts as superseded\n")

        print("  judged at filing level (wrong):")
        by_filing = {}
        for period in ("2024-12-31", "2023-12-31"):
            print(f"    {period}")
            got = rows(Q2_BY_FILING, seg=seg, concept="Revenues", period_end=period)
            expect(got, f"{period}: no facts at all — the MATCH found nothing")
            show(got)
            by_filing[period] = got
        print("  FY2023 has no current value. The amendment restated FY2024 only,")
        print("  but every fact in the amended filing comes back stale, so a")
        print("  year-over-year comparison has no baseline.\n")
        expect(all(r["superseded_by"] for r in by_filing["2023-12-31"]),
               "finding 2 stopped reproducing: filing-level judgement left FY2023 "
               "with a current value, so the bug is gone. Check whether AMENDS "
               "changed grain before deleting this check.")

        print("  judged by fact-level replacement (correct):")
        live = {}
        for period in ("2024-12-31", "2023-12-31"):
            print(f"    {period}")
            got = rows(Q2_BY_FACT, seg=seg, concept="Revenues", period_end=period)
            expect(got, f"{period}: no facts at all — the MATCH found nothing")
            show(got)
            live[period] = current(got, period)
        print()

        a = live["2023-12-31"]["value"]
        b = live["2024-12-31"]["value"]
        stale = 412_000_000.0
        expect(live["2024-12-31"]["basis"] == "as_restated",
               "FY2024 current value is not the restated one")
        expect(b != stale,
               "FY2024 came back as the superseded figure — the correct query is "
               "returning the wrong number")
        print("COMPARISON — computed at query time, never stored")
        print(f"  FY2023 ${a:,.0f} -> FY2024 ${b:,.0f}   {(b - a) / a * 100:+.1f}%")
        print(f"  quoting the superseded figure instead gives "
              f"{(stale - a) / a * 100:+.1f}% — also defensible from the text,")
        print("  and wrong. The AMENDS edge is the only thing that distinguishes them.")


if __name__ == "__main__":
    main()
