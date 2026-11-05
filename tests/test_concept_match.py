"""What the structured arm does with the question it is given.

Before this, it did nothing with it: the SQL filtered, ordered by a hash, and
scored every row 1.0, so "Intelligent Cloud revenue" returned earnings per
share. These run on a bare checkout; none of them need a database.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "structured"))

import concept_match  # noqa: E402
import structured_query_v1  # noqa: E402


# ---- reading the question -------------------------------------------------

def test_a_concept_name_splits_into_words():
    assert concept_match.concept_words("us-gaap:NetIncomeLoss") == ["net", "income", "loss"]
    assert concept_match.concept_words("msft:IntelligentCloudMember") == [
        "intelligent", "cloud", "member"
    ]


def test_question_words_drop_the_scaffolding():
    assert concept_match.question_terms("What was the revenue?") == ["revenue"]


def test_a_company_name_is_kept_and_only_dilutes():
    """Company names are not stopped: there are ten filers in the corpus and a
    hardcoded list would be wrong for nine of them. The name matches no
    concept, so it lowers coverage slightly and never changes the winner.
    Narrowing by company is the ticker filter's job."""
    terms = concept_match.question_terms("What was Microsoft's revenue?")
    assert terms == ["microsoft", "revenue"]
    alias = concept_match.aliased_concept(terms)
    assert alias == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def test_singular_and_plural_are_the_same_word():
    """Filers tag both Revenues and RevenueFromContract..., so a question
    about revenue has to reach both."""
    terms = concept_match.question_terms("revenue")
    assert concept_match.score_fact(terms, "us-gaap:Revenues", "[]", None) > 0


def test_a_bare_year_is_not_a_search_term():
    """A year is a period filter. Left in, every fact tagged 2025 looks relevant."""
    assert "2025" not in concept_match.question_terms("revenue for fiscal year 2025")
    assert concept_match.question_terms("revenue for fiscal year 2025") == ["revenue"]


def test_a_number_that_is_not_a_year_survives():
    assert "401" in concept_match.question_terms("401 k plan contributions")


# ---- the alias table ------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("operating cash flow", "us-gaap:NetCashProvidedByUsedInOperatingActivities"),
    ("investing cash flow", "us-gaap:NetCashProvidedByUsedInInvestingActivities"),
    ("net income", "us-gaap:NetIncomeLoss"),
    ("total assets", "us-gaap:Assets"),
    ("revenue", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
])
def test_known_names_resolve_to_their_tag(question, expected):
    terms = concept_match.question_terms(question)
    assert concept_match.aliased_concept(terms) == expected


def test_the_longest_matching_phrase_wins():
    """'operating cash flow' must not resolve as 'operating income'."""
    terms = concept_match.question_terms("what was the operating cash flow")
    assert concept_match.aliased_concept(terms) == (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    )


def test_an_unknown_phrase_resolves_to_nothing():
    assert concept_match.aliased_concept(["unrecognised", "widgets"]) is None


# ---- scoring --------------------------------------------------------------

def test_a_known_name_outranks_a_lucky_word_overlap():
    """IncomeTaxesPaidNet contains both 'net' and 'income' and is not the answer."""
    terms = concept_match.question_terms("net income")
    alias = concept_match.aliased_concept(terms)
    known = concept_match.score_fact(terms, "us-gaap:NetIncomeLoss", "[]", alias)
    lucky = concept_match.score_fact(terms, "us-gaap:IncomeTaxesPaidNet", "[]", alias)
    assert known > lucky


def test_a_fact_with_no_shared_words_scores_zero():
    terms = concept_match.question_terms("revenue")
    assert concept_match.score_fact(terms, "us-gaap:Assets", "[]", None) == 0.0


def test_a_dimension_cannot_create_a_match_on_its_own():
    """StockholdersEquity carries an AccumulatedGainLossNetCashFlowHedge member,
    which made it the top hit for 'operating cash flow'. A qualifier is not a
    subject."""
    terms = concept_match.question_terms("operating cash flow")
    dims = json.dumps([{"axis": "us-gaap:StatementEquityComponentsAxis",
                        "member": "us-gaap:AccumulatedGainLossNetCashFlowHedgeMember"}])
    assert concept_match.score_fact(terms, "us-gaap:StockholdersEquity", dims, None) == 0.0


def test_a_dimension_sharpens_a_match_it_did_not_create():
    terms = concept_match.question_terms("Intelligent Cloud revenue")
    alias = concept_match.aliased_concept(terms)
    concept = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    dims = json.dumps([{"axis": "us-gaap:StatementBusinessSegmentsAxis",
                        "member": "msft:IntelligentCloudMember"}])
    assert (concept_match.score_fact(terms, concept, dims, alias)
            > concept_match.score_fact(terms, concept, "[]", alias))


def test_the_plainest_concept_wins_a_tie():
    terms = concept_match.question_terms("total assets")
    plain = concept_match.score_fact(terms, "us-gaap:Assets", "[]", None)
    qualified = concept_match.score_fact(terms, "us-gaap:AssetsHeldForSaleNotPartOfDisposalGroup", "[]", None)
    assert plain > qualified


def test_malformed_dimensions_do_not_take_the_query_down():
    terms = concept_match.question_terms("revenue")
    assert concept_match.score_fact(terms, "us-gaap:Revenues", "not json", None) > 0


def test_an_empty_question_scores_nothing():
    assert concept_match.score_fact([], "us-gaap:Assets", "[]", None) == 0.0


# ---- ranking --------------------------------------------------------------

def _record(fact_id, concept, period_end="2026-06-30", dimensions="[]"):
    return {"fact_id": fact_id, "concept_name": concept,
            "period_end": period_end, "dimensions": dimensions}


def test_facts_that_do_not_answer_the_question_are_dropped():
    """Returning an unrelated fact is worse than returning nothing. An empty
    list is a real answer under the contract."""
    records = [_record("a", "us-gaap:Assets"), _record("b", "us-gaap:NetIncomeLoss")]
    ranked = structured_query_v1.rank_records(records, "operating cash flow", {}, 10)
    assert ranked == []


def test_an_explicit_concept_filter_skips_scoring():
    """The caller naming a concept is a stronger statement than the wording of
    the question, so every row they asked for comes back."""
    records = [_record("a", "us-gaap:Assets")]
    ranked = structured_query_v1.rank_records(
        records, "something else entirely", {"concept": "us-gaap:Assets"}, 10
    )
    assert [r["fact_id"] for _, r in ranked] == ["a"]


def test_equally_relevant_facts_come_back_newest_first():
    """The same concept across three years ties exactly on score. Which three
    survive top_k is decided here, so it should be the newest three."""
    records = [
        _record("c", "us-gaap:Assets", "2024-06-30"),
        _record("a", "us-gaap:Assets", "2026-06-30"),
        _record("b", "us-gaap:Assets", "2025-06-30"),
    ]
    ranked = structured_query_v1.rank_records(records, "total assets", {}, 3)
    assert [r["period_end"] for _, r in ranked] == ["2026-06-30", "2025-06-30", "2024-06-30"]


def test_a_missing_period_sorts_last_rather_than_raising():
    records = [_record("a", "us-gaap:Assets", None), _record("b", "us-gaap:Assets", "2025-06-30")]
    ranked = structured_query_v1.rank_records(records, "total assets", {}, 2)
    assert [r["fact_id"] for _, r in ranked] == ["b", "a"]


def test_ranking_is_identical_between_runs():
    records = [_record(x, "us-gaap:Assets") for x in ("c", "a", "b")]
    first = structured_query_v1.rank_records(records, "total assets", {}, 3)
    second = structured_query_v1.rank_records(list(reversed(records)), "total assets", {}, 3)
    assert [r["fact_id"] for _, r in first] == [r["fact_id"] for _, r in second]


def test_top_k_is_respected():
    records = [_record(x, "us-gaap:Assets") for x in "abcdef"]
    assert len(structured_query_v1.rank_records(records, "total assets", {}, 2)) == 2
