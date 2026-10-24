"""Schema-probe invariants that hold without a database.

`toy_graph.py --check-only` already runs all of these. Having them here as well
means `pytest tests/` covers them, which is the one command CI runs, and a
failure names the invariant that broke instead of pointing at a line number.

Everything the probe needs at import time is in the standard library, so these
run on a bare checkout with no Neo4j and no extra packages.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toy_graph"))

import toy_graph as tg  # noqa: E402


TEXT_EDGES = [e for e in tg.EDGES if e[3]["prov_shape"] == "text"]


def test_node_keys_are_unique():
    keys = [n[0] for n in tg.NODES]
    assert len(keys) == len(set(keys))


def test_every_edge_has_both_endpoints():
    keys = {n[0] for n in tg.NODES}
    for src, dst, rel, _ in tg.EDGES:
        assert src in keys, f"{rel} has no source node"
        assert dst in keys, f"{rel} has no target node"


def test_edge_keys_are_unique():
    seen = set()
    for src, dst, rel, props in tg.EDGES:
        eid = tg.edge_key(src, rel, dst, props)
        assert eid not in seen, f"{rel} collides with an earlier edge"
        seen.add(eid)


@pytest.mark.parametrize("src,dst,rel,props", TEXT_EDGES,
                         ids=[f"{e[2]}" for e in TEXT_EDGES])
def test_edge_key_survives_a_rechunk(src, dst, rel, props):
    """The invariant the whole key scheme rests on.

    Data re-chunks between W6 and W9. If a chunk id or a character offset gets
    back into the edge key, the same sentence MERGEs as a second edge on the
    next parse and the graph silently doubles.
    """
    rechunked = {**props,
                 "prov_source_chunk_id": "new-opaque-chunk",
                 "prov_char_start": 999_999}
    assert tg.edge_key(src, rel, dst, rechunked) == tg.edge_key(src, rel, dst, props)


def test_text_edges_carry_accession_and_section():
    for src, dst, rel, props in TEXT_EDGES:
        assert props.get("prov_accession"), f"{rel} cannot be cited: no accession"
        assert props.get("prov_section_id"), f"{rel} cannot be cited: no section"


def test_facts_carry_no_values():
    """Numbers live in the fact table. The graph holds the pointer only."""
    for _, label, props in tg.NODES:
        if label == "Fact":
            assert "value" not in props


def test_label_set_matches_the_settled_ontology():
    assert set(tg.LABELS) == {"Company", "Filing", "Section", "Segment",
                              "Subsidiary", "Fact", "Claim", "RiskFactor"}


def test_claim_examples_parsed():
    assert len(tg.CLAIM_KEYS) == 6
    assert {r["claim_type"] for r in tg.CLAIM_RECORDS} == {
        "stated_fact", "forward_looking", "hedged_risk"}


def test_every_replacement_names_a_fact_on_both_sides():
    """Supersession is fact-level, so both halves have to exist."""
    for replacement, original in tg.FACT_REPLACEMENTS.items():
        assert replacement in tg.FACT_VALUES
        assert original in tg.FACT_VALUES
