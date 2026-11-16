"""Tests for the corpus report.

Everything here runs against a fake query runner and an in-memory DuckDB, so CI
covers it with no Aura and no built store. The report's own checks have already
been wrong once - a quarterly span threshold near one quarter flagged 1,528
legitimate year-to-date facts - so the thresholds are tested rather than trusted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import corpus_report as cr  # noqa: E402

duckdb = pytest.importorskip("duckdb")


class FakeGraph:
    """Answers Cypher by matching on a fragment of the query text.

    Crude on purpose: the point is to check the report's own logic - scope
    choice, warnings, shaping - not to reimplement Neo4j.
    """

    def __init__(self, answers: dict[str, list[dict]]):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, cypher: str, **params):
        self.calls.append((cypher, params))
        for fragment, rows in self.answers.items():
            if fragment in cypher:
                return rows
        return []


# --------------------------------------------------------------------------
# scope


def test_single_scope_is_chosen_without_a_warning():
    scope, warnings = cr.pick_scope([{"scope": "run-v1", "nodes": 10}], None)
    assert scope == "run-v1"
    assert warnings == []


def test_a_second_scope_raises_a_loud_warning():
    scopes = [{"scope": "run-v1", "nodes": 10_000}, {"scope": "run-v2", "nodes": 9_000}]
    scope, warnings = cr.pick_scope(scopes, None)
    assert scope == "run-v1"
    assert len(warnings) == 1
    assert "MORE THAN ONE SCOPE" in warnings[0]
    assert "run-v2" in warnings[0]


def test_an_explicit_scope_is_honoured():
    scopes = [{"scope": "run-v1", "nodes": 10}, {"scope": "run-v2", "nodes": 20}]
    scope, _ = cr.pick_scope(scopes, "run-v2")
    assert scope == "run-v2"


def test_an_unknown_scope_stops_rather_than_reporting_zero():
    with pytest.raises(SystemExit):
        cr.pick_scope([{"scope": "run-v1", "nodes": 10}], "typo")


def test_no_scope_at_all_is_reported_not_guessed():
    scope, warnings = cr.pick_scope([], None)
    assert scope is None
    assert warnings and "no pipeline_scope" in warnings[0]


def test_every_graph_query_filters_on_scope():
    """The whole point of the scope selector. If one query forgets the filter it
    silently sums two extraction runs together."""
    fake = FakeGraph({
        "db.labels": [{"label": "Claim", "c": 5}],
        "db.relationshipTypes": [{"type": "SUPPORTS", "c": 5}],
        "count(f) AS ten_ks": [{"ten_ks": 1}],
    })
    cr.graph_counts(fake, "run-v1")
    cr.graph_orphans(fake, "run-v1")
    cr.graph_spread(fake, "run-v1")
    cr.subsidiary_coverage(fake, "run-v1")
    cr.graph_identity(fake, "run-v1")
    cr.graph_status(fake, "run-v1")
    assert fake.calls
    for cypher, params in fake.calls:
        assert "pipeline_scope = $scope" in cypher, cypher
        assert params.get("scope") == "run-v1"


# --------------------------------------------------------------------------
# shaping


def test_spread_reports_the_range_not_just_the_total():
    assert cr._spread([1, 1, 5, 800]) == {
        "n": 4, "min": 1, "median": 3.0, "max": 800, "total": 807}


def test_spread_of_nothing_does_not_divide_by_zero():
    assert cr._spread([]) == {"n": 0}


def test_zero_counts_are_dropped_from_labels():
    """A label with no nodes in this scope is noise, not a finding."""
    fake = FakeGraph({
        "db.labels": [{"label": "Claim", "c": 5}, {"label": "Subsidiary", "c": 0}],
        "db.relationshipTypes": [{"type": "OWNS", "c": 0}],
    })
    counts = cr.graph_counts(fake, "run-v1")
    assert counts["nodes_by_label"] == {"Claim": 5}
    assert counts["relationships_by_type"] == {}


# --------------------------------------------------------------------------
# provenance


def test_provenance_names_the_unvalidated_source():
    lines = cr.provenance_lines(
        "internal", {"AURA_INSTANCENAME": "two"},
        {"scope": "run-v1", "scopes": {"run-v1": 1}},
        {"meta": {"corpus_version": "data-api-raw:20", "source": "data-api:raw"}},
        "/tmp/store.duckdb")
    assert any("UNVALIDATED" in line for line in lines)


def test_provenance_calls_out_a_store_with_no_source_key():
    """The EDGAR store is identified by what it does not write, which is fragile
    enough that the report should say it out loud."""
    lines = cr.provenance_lines(
        "internal", {}, {"scope": "run-v1", "scopes": {"run-v1": 1}},
        {"meta": {"corpus_version": "edgar-xbrl-v1:20:fae42938"}}, "/tmp/old.duckdb")
    assert any("deprecated EDGAR store" in line for line in lines)


# --------------------------------------------------------------------------
# cross arm


def test_cross_arm_shows_a_filing_missing_from_the_graph():
    graph = {"spread": {"claims_per_filing": {"acc-1": 40}}}
    store = {"per_filing": [
        {"accession": "acc-1", "ticker": "AAA", "facts": 100},
        {"accession": "acc-2", "ticker": "BBB", "facts": 90},
    ]}
    rows = cr.cross_arm(graph, store)
    assert {r["accession"]: r["claims"] for r in rows} == {"acc-1": 40, "acc-2": 0}


def test_cross_arm_shows_a_filing_missing_from_the_store():
    graph = {"spread": {"claims_per_filing": {"acc-1": 40, "acc-9": 7}}}
    store = {"per_filing": [{"accession": "acc-1", "ticker": "AAA", "facts": 100}]}
    rows = cr.cross_arm(graph, store)
    orphan = [r for r in rows if r["accession"] == "acc-9"][0]
    assert orphan["facts"] == 0 and orphan["claims"] == 7


# --------------------------------------------------------------------------
# the store, against a real but tiny DuckDB


@pytest.fixture
def store():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE store_meta_v1 (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO store_meta_v1 VALUES ('corpus_version','test:2'),('source','data-api:raw')")
    con.execute("""CREATE TABLE filings_v1 (accession_number VARCHAR, cik VARCHAR,
        ticker VARCHAR, company_name VARCHAR, form VARCHAR, filing_date VARCHAR,
        loader_version VARCHAR)""")
    con.execute("""INSERT INTO filings_v1 VALUES
        ('acc-k','1','AAA','A Corp','10-K','2026-01-01','v1'),
        ('acc-q','1','AAA','A Corp','10-Q','2026-04-01','v1')""")
    con.execute("""CREATE TABLE financial_facts_v1 (cik VARCHAR, ticker VARCHAR,
        accession_number VARCHAR, filing_date VARCHAR, concept_name VARCHAR,
        context_ref VARCHAR, period_start VARCHAR, period_end VARCHAR,
        period_type VARCHAR, dimensions VARCHAR, raw_value VARCHAR, unit VARCHAR,
        scale VARCHAR, sign VARCHAR, numeric_value VARCHAR, fact_id VARCHAR)""")
    con.execute("""INSERT INTO financial_facts_v1 VALUES
        -- the 10-K: revenue consolidated, and the same concept as a segment slice
        ('1','AAA','acc-k','2026-01-01','us-gaap:Revenues','c1','2025-01-01','2025-12-31',
         'duration','[]','100','USD',NULL,NULL,'100','f1'),
        ('1','AAA','acc-k','2026-01-01','us-gaap:Revenues','c2','2025-01-01','2025-12-31',
         'duration','[{"axis":"us-gaap:StatementBusinessSegmentsAxis","member":"x:CloudMember"}]',
         '60','USD',NULL,NULL,'60','f2'),
        ('1','AAA','acc-k','2026-01-01','us-gaap:Assets','c3','','2025-12-31',
         'instant','[]','900','USD',NULL,NULL,'900','f3'),
        -- a nine month year-to-date figure on the 10-Q. Legitimate, must not flag.
        ('1','AAA','acc-q','2026-04-01','us-gaap:Revenues','c4','2025-07-01','2026-03-31',
         'duration','[]','75','USD',NULL,NULL,'75','f4'),
        -- an eight year span on a 10-Q. Wrong, must flag.
        ('1','AAA','acc-q','2026-04-01','us-gaap:Revenues','c5','2018-01-01','2026-03-31',
         'duration','[]','9','USD',NULL,NULL,'9','f5'),
        -- a text fact with no number. Correct, not damage.
        ('1','AAA','acc-q','2026-04-01','us-gaap:SomethingTextBlock','c6','','2026-03-31',
         'instant','[]','see note 4','',NULL,NULL,NULL,'f6'),
        -- a parseable number that never reached numeric_value. A real defect.
        ('1','AAA','acc-q','2026-04-01','us-gaap:OtherThing','c7','','2026-03-31',
         'instant','[]','42','USD',NULL,NULL,NULL,'f7')""")
    yield con
    con.close()


def test_totals(store):
    assert cr.store_totals(store) == {
        "facts": 7, "filings": 2, "companies": 1, "concepts": 4}


def test_per_filing_counts_the_dimensioned_share(store):
    rows = {r["accession"]: r for r in cr.store_per_filing(store)}
    assert rows["acc-k"]["facts"] == 3
    assert rows["acc-k"]["dimensioned"] == 1
    assert rows["acc-k"]["dimensioned_share"] == pytest.approx(0.333, abs=0.001)
    assert rows["acc-q"]["dimensioned"] == 0


def test_a_year_to_date_quarter_is_not_flagged_but_an_eight_year_span_is(store):
    """The threshold this test pins got it wrong once: at 200 days it flagged
    1,528 legitimate six and nine month figures across the real corpus."""
    dates = cr.store_date_checks(store)
    assert dates["quarterly_filing_over_a_year"] == 1
    assert dates["start_after_end"] == 0
    assert dates["missing_period_end"] == 0


def test_a_missing_number_is_separated_from_a_fact_that_has_none(store):
    numeric = cr.store_column_population(store)["__numeric__"]
    assert numeric["no_numeric_value"] == 2      # the text block and the defect
    assert numeric["unparsed_numbers"] == 1      # only the defect


def test_an_all_null_column_is_named(store):
    columns = cr.store_column_population(store)
    assert columns["scale"]["all_null"] is True
    assert columns["unit"]["all_null"] is False


def test_key_concepts_reports_the_filing_that_is_missing_one(store):
    concepts = cr.store_key_concepts(store)
    assert concepts["missing"]["net income"] == ["acc-k", "acc-q"]
    assert "revenue" not in concepts["missing"]
    assert concepts["present"]["assets"] == {"acc-k": True, "acc-q": False}


def test_duplicates_separate_a_loader_bug_from_a_comparative(store):
    dups = cr.store_duplicates(store)
    assert dups["repeated_fact_id"] == 0
    # Both filings carry Revenues, but over different periods, so they are not
    # the same economic fact. Nothing here should be counted as a comparative.
    assert dups["comparative_groups"] == 0


def test_dimension_mix_separates_consolidated_from_sliced(store):
    mix = cr.store_dimension_mix(store)
    revenue = [r for r in mix["by_concept"] if r["concept"] == "us-gaap:Revenues"][0]
    assert revenue["consolidated"] == 3
    assert revenue["dimensioned"] == 1
    assert "us-gaap:StatementBusinessSegmentsAxis" in mix["axes"]


def test_meta_reads_the_store_identity(store):
    assert cr.store_meta(store)["source"] == "data-api:raw"


def test_a_store_with_no_meta_table_says_so():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE financial_facts_v1 (fact_id VARCHAR)")
    assert "__missing__" in cr.store_meta(con)
    con.close()
