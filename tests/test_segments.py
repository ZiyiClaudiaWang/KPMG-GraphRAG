"""Where the segment filter gets its vocabulary.

It used to get it from a literal dict holding three Microsoft members, so the
filter raised "Unknown segment" for the other nine companies in the corpus.
These build a small store in memory and check the dictionary is read out of
the facts instead; none of them need the real corpus on disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "structured"))

import segments  # noqa: E402

MEMBERS = [
    "msft:IntelligentCloudMember",
    "aapl:GreaterChinaSegmentMember",
    "casy:ReportableSegmentMember",
    "nke:NIKEBrandMember",
    "mu:CDBUMember",
    "ntap:PublicCloudMember",
    "ntap:PublicCloudSegmentMember",
    "us-gaap:AllOtherSegmentsMember",
]


@pytest.fixture
def store(tmp_path):
    """A store holding one fact per segment member, and one with no segment."""
    path = tmp_path / "facts.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE financial_facts_v1 (fact_id VARCHAR, dimensions VARCHAR)")
    rows = [
        (member, json.dumps([{"axis": segments.SEGMENT_AXIS, "member": member}]))
        for member in MEMBERS
    ]
    # A consolidated fact carries no dimensions, and must not become a segment.
    rows.append(("consolidated", "[]"))
    # A dimension on another axis is not a segment either.
    rows.append((
        "geography",
        json.dumps([{"axis": "us-gaap:StatementGeographicalAxis", "member": "country:US"}]),
    ))
    conn.executemany("INSERT INTO financial_facts_v1 VALUES (?, ?)", rows)
    conn.close()
    segments._CACHE_KEY = None  # each test builds its own dictionary
    return duckdb.connect(str(path), read_only=True), str(path)


# ---- reading a member name ------------------------------------------------

def test_a_member_becomes_the_name_a_person_would_use():
    assert segments.label_for("aapl:GreaterChinaSegmentMember") == "Greater China Segment"
    assert segments.label_for("msft:IntelligentCloudMember") == "Intelligent Cloud"


def test_the_filers_capitals_survive():
    """Title-casing these would give "Cdbu" and "Nike Brand"."""
    assert segments.label_for("mu:CDBUMember") == "CDBU"
    assert segments.label_for("nke:NIKEBrandMember") == "NIKE Brand"


def test_the_suffix_is_optional_when_looking_a_segment_up():
    """Both spellings are keys, so a caller does not have to know the tag."""
    assert "greater china" in segments._keys("aapl:GreaterChinaSegmentMember")
    assert "greater china segment" in segments._keys("aapl:GreaterChinaSegmentMember")


def test_the_label_keeps_the_suffix_when_dropping_it_would_say_nothing():
    """"Reportable" on its own names nothing, so the label reads in full."""
    assert segments.label_for("casy:ReportableSegmentMember") == "Reportable Segment"


# ---- the dictionary -------------------------------------------------------

def test_every_company_in_the_store_gets_a_segment(store):
    conn, path = store
    labels = set()
    for member in MEMBERS:
        assert segments.resolve(conn, segments.label_for(member), path) != []
        labels.add(member.split(":")[0])
    assert labels == {"msft", "aapl", "casy", "nke", "mu", "ntap", "us-gaap"}


def test_three_spellings_reach_the_same_member(store):
    conn, path = store
    member = ["aapl:GreaterChinaSegmentMember"]
    assert segments.resolve(conn, "Greater China", path) == member
    assert segments.resolve(conn, "Greater China Segment", path) == member
    assert segments.resolve(conn, "aapl:GreaterChinaSegmentMember", path) == member


def test_matching_ignores_case_and_extra_spaces(store):
    conn, path = store
    assert segments.resolve(conn, "  intelligent   CLOUD ", path) == [
        "msft:IntelligentCloudMember"
    ]


def test_one_label_can_name_more_than_one_member(store):
    """NetApp tags the same segment two ways. Returning one drops half the rows."""
    conn, path = store
    assert segments.resolve(conn, "Public Cloud", path) == [
        "ntap:PublicCloudMember",
        "ntap:PublicCloudSegmentMember",
    ]


def test_only_the_segment_axis_counts(store):
    conn, path = store
    assert "country:US" not in str(segments.load_dictionary(conn, path))
    with pytest.raises(ValueError):
        segments.resolve(conn, "US", path)


def test_an_unknown_segment_says_what_the_store_holds(store):
    conn, path = store
    with pytest.raises(ValueError) as caught:
        segments.resolve(conn, "Cloud", path)
    message = str(caught.value)
    assert "Unknown segment: Cloud" in message
    assert "Intelligent Cloud" in message


def test_a_store_with_no_facts_table_is_not_a_crash(tmp_path):
    segments._CACHE_KEY = None
    path = tmp_path / "empty.duckdb"
    conn = duckdb.connect(str(path))
    assert segments.load_dictionary(conn, str(path)) == {}
    with pytest.raises(ValueError, match="no segment facts are loaded"):
        segments.resolve(conn, "Intelligent Cloud", str(path))


def test_rebuilding_the_store_rebuilds_the_dictionary(store, tmp_path):
    """The cache is keyed on the file, so a corpus rebuild is picked up."""
    conn, path = store
    assert segments.resolve(conn, "Intelligent Cloud", path)

    rebuilt = tmp_path / "rebuilt.duckdb"
    writer = duckdb.connect(str(rebuilt))
    writer.execute("CREATE TABLE financial_facts_v1 (fact_id VARCHAR, dimensions VARCHAR)")
    writer.execute(
        "INSERT INTO financial_facts_v1 VALUES (?, ?)",
        ["new", json.dumps([{"axis": segments.SEGMENT_AXIS, "member": "tsla:EnergyMember"}])],
    )
    writer.close()

    fresh = duckdb.connect(str(rebuilt), read_only=True)
    assert segments.resolve(fresh, "Energy", str(rebuilt)) == ["tsla:EnergyMember"]
    with pytest.raises(ValueError):
        segments.resolve(fresh, "Intelligent Cloud", str(rebuilt))
