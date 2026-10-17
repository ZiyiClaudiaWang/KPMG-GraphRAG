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
        def rows(q, **params):
            got = [r.data() for r in session.run(q, **params)]
            for record in got:
                if "fact_key" in record:
                    record["value"] = tg.FACT_VALUES[record["fact_key"]]
            return got

        print("FINDING 1 — subsidiary to segment\n")

        print("  historical parent-only traversal:")
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
        expect(hit["section_id"] and hit["accession_number"], "Path hop lacks stable provenance")
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
            got = rows(Q2_BY_FACT, seg=seg, concept="Revenues", period_end=period,
                       superseded_keys=validated_superseded_keys())
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
        print("  and wrong. AMENDS plus explicit fact-level replacement identifies the current figure.")
        expect(round((b-a)/a*100, 1) == 35.6, "Unexpected corrected growth")
        print("\nCLAIMS — source, qualifiers, context and candidate themes")
        claims = rows("""
          MATCH (:Company {cik:$cik})-[:FILES]->(filing:Filing)-[:CONTAINS]->(s:Section)
                -[support:SUPPORTS]->(claim:Claim)-[theme:CONCERNS_RISK]->(risk:RiskFactor)
          WHERE claim.probe_id=$probe
          RETURN claim.example_id AS example_id, claim.quote AS quote,
                 claim.claim_type AS claim_type, claim.context AS context,
                 claim.subject_scope AS subject_scope, claim.annotation_note AS note,
                 support.prov_section_id AS section_id, support.prov_accession AS accession,
                 theme.prov_section_id AS theme_section, risk.risk_id AS risk_id
          ORDER BY example_id
        """, cik=tg.APPLE, probe=tg.PROBE_ID)
        expect(len(claims) == 6, "Expected six Claim-to-theme paths")
        for claim in claims:
            expect(claim["quote"] and claim["section_id"] and claim["accession"], "Uncitable Claim")
            expect(claim["theme_section"] == claim["section_id"], "Uncitable theme assignment")
            if claim["example_id"] == "05":
                expect(claim["context"], "Pronoun example lost its context")
            if claim["example_id"] == "04":
                expect("Global" in claim["subject_scope"] and claim["note"], "Global/mixed scope lost")
            print(f"  {claim['example_id']}: {claim['claim_type']} -> {claim['risk_id']}")
        values = rows("MATCH (f:Fact {probe_id:$probe}) WHERE f.value IS NOT NULL RETURN f.key AS k",
                      probe=tg.PROBE_ID)
        expect(not values, "Numeric fixture values leaked into Neo4j")
        print("All findings and Claim checks passed. Numeric values came from the local fixture lookup.")


if __name__ == "__main__":
    main()
