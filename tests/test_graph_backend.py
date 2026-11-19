"""What the graph arm must do, with and without a database.

Everything above `test_live_*` runs on a bare checkout. The live tests need a
Neo4j holding a loaded extraction run and skip themselves when there is none,
so a laptop without Docker still gets the rest.
"""

from __future__ import annotations

import os

import pytest

from graphrag import contract, graph_backend, retrieve


@pytest.fixture(autouse=True)
def clear_backends():
    retrieve.configure_graph_backend(None)
    yield
    retrieve.configure_graph_backend(None)


def _row(**overrides):
    """One Cypher row, shaped the way SEARCH_CYPHER returns it."""
    row = {
        "claim_id": "a552be2d-cecf-5cac-86fc-1d09eaa3d241",
        "quote": "Microsoft is a technology company.",
        "claim_type": "stated_fact",
        "subject_scope": None,
        "extraction_status": "needs_review",
        "section_id": "e358f4a0-fed6-4530-885f-66ea84d58285",
        "score": 3.5,
        "section_key": "part-i-item-1",
        "section_title": "ITEM 1. BUSINESS",
        "section_type": "business",
        "section_xpath": "/html/body/div[9]/p[4]",
        "raw_spans_json": "[[[27, 178]]]",
        "section_path": "Part I Item 1 > GENERAL",
        "sentence_ids": ["0001193125-26-323660|part-i-item-1|s0003"],
        "context_unit_ids": None,
        "risk_lead_unit_id": None,
        "risk_lead_sentence": None,
        "risk_summary": None,
        "multi_fragment": None,
        "role": "body",
        "model": "gpt-4.1-mini-2025-04-14",
        "accession_number": "0001193125-26-323660",
        "form": "10-K",
        "filing_date": "2026-07-29",
        "reporting_period_end": "2026-06-30",
        "cik": "0000789019",
        "company_name": "Microsoft Corporation",
        "ticker": "MSFT",
    }
    row.update(overrides)
    return row


# ---- the envelope ---------------------------------------------------------

def test_a_claim_becomes_contract_shaped_evidence():
    item = graph_backend._to_evidence(_row(), trace_id="q-1")
    item["rank"] = 1
    contract.validate_evidence(item)


def test_the_provenance_key_is_the_section_and_nothing_else():
    item = graph_backend._to_evidence(_row(), trace_id=None)
    assert item["chunk_id"] is None
    assert item["section_id"]
    assert "fact_id" not in item["metadata"]


def test_claim_type_and_subject_scope_reach_the_caller():
    """Issue #51. Without scope, a claim about a competitor reads as one
    about the filer."""
    item = graph_backend._to_evidence(
        _row(claim_type="hedged_risk", subject_scope="competitors"), trace_id=None
    )
    assert item["metadata"]["claim_type"] == "hedged_risk"
    assert item["metadata"]["subject_scope"] == "competitors"


def test_source_pointer_survives_either_loader():
    """The extraction pipeline is swapping sentence ids for unit ids. Cypher
    returns null for a property that is not there instead of failing, so
    reading only one name would lose the pointer without anything breaking."""
    old = graph_backend._to_evidence(_row(), trace_id=None)
    assert old["metadata"]["source_unit_ids"] == [
        "0001193125-26-323660|part-i-item-1|s0003"
    ]

    new = graph_backend._to_evidence(
        _row(sentence_ids=None, context_unit_ids=["u-1", "u-2"]), trace_id=None
    )
    assert new["metadata"]["source_unit_ids"] == ["u-1", "u-2"]


def test_a_claim_with_neither_pointer_gives_an_empty_list():
    item = graph_backend._to_evidence(
        _row(sentence_ids=None, context_unit_ids=None), trace_id=None
    )
    assert item["metadata"]["source_unit_ids"] == []


def test_spans_arrive_as_a_list_not_as_json_text():
    item = graph_backend._to_evidence(_row(), trace_id=None)
    assert item["metadata"]["raw_spans"] == [[[27, 178]]]


def test_unreadable_spans_do_not_take_the_result_down():
    assert graph_backend._parse_spans("not json") == []
    assert graph_backend._parse_spans(None) == []


def test_source_url_points_at_the_filing_on_edgar():
    assert graph_backend.source_url("0000789019", "0001193125-26-323660") == (
        "https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/"
    )


def test_cik_is_padded_back_to_ten_digits():
    item = graph_backend._to_evidence(_row(cik="789019"), trace_id=None)
    assert item["cik"] == "0000789019"
    item["rank"] = 1
    contract.validate_evidence(item)


# ---- filters --------------------------------------------------------------

def test_supported_filters_become_cypher_and_parameters():
    where, params = graph_backend._where_clause({"ticker": "MSFT", "form": "10-K"})
    assert where == "co.ticker = $ticker AND fl.form = $form"
    assert params == {"ticker": "MSFT", "form": "10-K"}


@pytest.mark.parametrize("key", ["is_table"])
def test_a_filter_the_graph_cannot_serve_is_refused_by_name(key):
    with pytest.raises(contract.FilterNotSupportedError, match=key):
        graph_backend._check_filters({key: "anything"})


def test_refusing_a_filter_says_why():
    with pytest.raises(contract.FilterNotSupportedError, match="prose only"):
        graph_backend._check_filters({"is_table": True})


@pytest.mark.parametrize("key", ["fiscal_year", "fiscal_period"])
def test_the_fiscal_filters_are_accepted_and_ignored_like_the_structured_arm(key):
    """Both arms are offered these keys by the contract. Refusing here while
    the structured arm ignored them meant one filter dict worked on one arm
    and raised on the other, and 129 of Eval's 155 questions carry one."""
    graph_backend._check_filters({key: "anything"})

    where, params = graph_backend._where_clause({"ticker": "MSFT", key: "anything"})
    assert where == "co.ticker = $ticker"
    assert params == {"ticker": "MSFT"}


def test_max_hops_below_the_traversal_is_refused():
    with pytest.raises(contract.FilterNotSupportedError, match="max_hops=1"):
        graph_backend._check_filters({"max_hops": 1})


def test_max_hops_at_or_above_the_traversal_is_fine():
    graph_backend._check_filters({"max_hops": graph_backend.TRAVERSAL_HOPS})
    graph_backend._check_filters({"max_hops": 10})


# ---- the error taxonomy ---------------------------------------------------

def test_a_refused_filter_is_not_reported_as_an_outage():
    """The wrapper used to turn every backend exception into
    RetrievalUnavailableError, so a filter the backend will never support
    looked exactly like a database that was briefly down. One is worth
    retrying; the other never is."""
    def backend(query, filters, top_k, trace_id):
        raise contract.FilterNotSupportedError("the graph arm cannot filter on fiscal_year")

    retrieve.configure_graph_backend(backend)
    with pytest.raises(contract.FilterNotSupportedError):
        retrieve.graph_search("revenue", filters={"fiscal_year": 2026})


def test_a_dead_store_is_still_reported_as_an_outage():
    def backend(query, filters, top_k, trace_id):
        raise ConnectionError("connection refused")

    retrieve.configure_graph_backend(backend)
    with pytest.raises(contract.RetrievalUnavailableError):
        retrieve.graph_search("revenue")


def test_filter_not_supported_is_still_a_value_error():
    """Callers written before this class existed catch ValueError."""
    assert issubclass(contract.FilterNotSupportedError, ValueError)


# ---- the query ------------------------------------------------------------

def test_lucene_syntax_in_a_question_is_stripped_not_executed():
    assert graph_backend._lucene_safe('revenue AND "cloud"~2') == "revenue OR AND OR cloud OR 2"
    # The trailing '?' goes too: Lucene reads it as a single-character wildcard.
    assert graph_backend._lucene_safe("What is Microsoft's revenue?") == (
        "What OR is OR Microsoft's OR revenue"
    )


def test_a_query_of_only_punctuation_returns_nothing_rather_than_failing():
    assert graph_backend._lucene_safe("*?:") == ""
    assert graph_backend.search("*?:") == []


# ---- against a real graph -------------------------------------------------

def _graph_available() -> bool:
    """A loaded extraction run, not just any database.

    The toy graph also has Claim nodes, so the check walks the whole path the
    envelope needs. Finding toy data and running these against it would fail
    for the wrong reason.
    """
    if not os.environ.get("NEO4J_URI"):
        return False
    try:
        driver = graph_backend.connect()
        with driver.session() as session:
            return session.run(
                "MATCH (:Company)-[:FILES]->(:Filing)-[:CONTAINS]->"
                "(:Section)-[:SUPPORTS]->(c:Claim) RETURN count(c) AS n"
            ).single()["n"] > 0
    except Exception:
        return False
    finally:
        graph_backend.close()


live = pytest.mark.skipif(
    not _graph_available(), reason="needs a Neo4j holding a loaded extraction run"
)


@live
def test_live_search_returns_contract_valid_evidence():
    graph_backend.ensure_index()
    retrieve.configure_graph_backend(graph_backend.search)
    rows = retrieve.graph_search("competition", top_k=5, trace_id="live-1")
    assert rows
    contract.validate_results(rows, top_k=5)
    assert all(r["metadata"]["trace_id"] == "live-1" for r in rows)


@live
def test_live_a_filter_that_matches_nothing_is_empty_not_an_error():
    """No evidence and no store are different answers. Eval scores them
    differently and the Agent should only retry one of them."""
    retrieve.configure_graph_backend(graph_backend.search)
    assert retrieve.graph_search("revenue", filters={"ticker": "NOSUCHTICKER"}) == []
