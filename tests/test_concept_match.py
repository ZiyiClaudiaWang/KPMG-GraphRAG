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


# ---- the filer's own name -------------------------------------------------

REVENUE = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
CONSOLIDATION = {"axis": "srt:ConsolidationItemsAxis",
                 "member": "us-gaap:OperatingSegmentsMember"}


def test_the_company_name_does_not_name_a_segment():
    """Nike files nke:NIKEBrandMember, so "Nike revenue" used to return a
    NIKE Brand slice rather than the company's own figure."""
    terms = concept_match.question_terms("Nike revenue")
    alias = concept_match.aliased_concept(terms)
    filer = concept_match.filer_words("NKE", "NIKE, Inc.")
    brand = json.dumps([CONSOLIDATION,
                        {"axis": SEGMENT_AXIS, "member": "nke:NIKEBrandMember"},
                        {"axis": "us-gaap:SubsegmentsAxis",
                         "member": "nke:NorthAmericaSegmentMember"}])
    assert (concept_match.score_fact(terms, REVENUE, "[]", alias, filer)
            > concept_match.score_fact(terms, REVENUE, brand, alias, filer))


def test_a_segment_total_beats_a_slice_of_that_segment():
    """Both carry ConverseSegmentMember. The one that also splits by sales
    channel answers a narrower question than the one that was asked."""
    terms = concept_match.question_terms("Converse revenue")
    alias = concept_match.aliased_concept(terms)
    filer = concept_match.filer_words("NKE", "NIKE, Inc.")
    converse = {"axis": SEGMENT_AXIS, "member": "nke:ConverseSegmentMember"}
    total = json.dumps([CONSOLIDATION, converse])
    slice_ = json.dumps([CONSOLIDATION, converse,
                         {"axis": "us-gaap:ContractWithCustomerSalesChannelAxis",
                          "member": "nke:SalesChannelOtherMember"}])
    assert (concept_match.score_fact(terms, REVENUE, total, alias, filer)
            > concept_match.score_fact(terms, REVENUE, slice_, alias, filer))


def test_legal_suffixes_are_not_treated_as_filer_words():
    """Otherwise "group revenue" would stop matching for any filer whose
    registered name ends in Group."""
    assert "nike" in concept_match.filer_words("NKE", "NIKE, Inc.")
    assert "inc" not in concept_match.filer_words("NKE", "NIKE, Inc.")


# --- The words a reader uses, and the facts that cannot answer them ---------

def test_the_words_a_reader_uses_reach_the_right_tag():
    """None of these share a word with the tag they name, so word overlap
    cannot find any of them. "Top line" used to reach a street address."""
    for question, concept in [
        ("Microsoft top line", REVENUE),
        ("Microsoft bottom line", "us-gaap:NetIncomeLoss"),
        ("Apple capex", "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"),
        ("Microsoft deferred revenue", "us-gaap:ContractWithCustomerLiabilityCurrent"),
        ("Microsoft share buybacks", "us-gaap:PaymentsForRepurchaseOfCommonStock"),
        ("Microsoft diluted EPS", "us-gaap:EarningsPerShareDiluted"),
        ("Apple effective tax rate",
         "us-gaap:EffectiveIncomeTaxRateContinuingOperations"),
    ]:
        terms = concept_match.question_terms(question)
        assert concept_match.aliased_concept(terms) == concept, question


def test_a_plural_reaches_the_same_tag_as_the_singular():
    """A reader types "capital expenditures", the table holds the singular."""
    singular = concept_match.question_terms("Apple capital expenditure")
    plural = concept_match.question_terms("Apple capital expenditures")
    assert (concept_match.aliased_concept(singular)
            == concept_match.aliased_concept(plural)
            == "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment")


def test_naming_both_current_and_total_means_current():
    """Two phrases of the same length, decided by their order in the table."""
    terms = concept_match.question_terms("Apple total current assets")
    assert concept_match.aliased_concept(terms) == "us-gaap:AssetsCurrent"


def test_no_alias_phrase_contains_a_stop_word():
    """question_terms drops stop words before the table is consulted, so a
    phrase holding "of" or "and" could never match anything."""
    for phrase, concept in concept_match.CONCEPT_ALIASES:
        for word in phrase:
            assert word not in concept_match.STOP_WORDS, (phrase, concept)


@pytest.fixture
def no_store(monkeypatch):
    """Rank rows without a database.

    rank_records reads company_name out of the store to tell a filer's own
    name from one of its segment names. These rows name their ticker, which
    is enough, and CI has no store to read.
    """
    monkeypatch.setattr(structured_query_v1, "_filing", lambda accession: {})


def _fact(concept, value, fact_id):
    return {"concept_name": concept, "dimensions": "[]", "numeric_value": value,
            "fact_id": fact_id, "ticker": "MSFT", "accession_number": "x",
            "period_start": "2025-07-01", "period_end": "2026-06-30"}


def test_a_fact_with_no_number_never_outranks_one_that_has_a_number(no_store):
    """A street address is tagged like any other fact and scores like one.
    Asking for Microsoft's top line returned EntityAddressAddressLine1 at
    rank 1, because "line" is a word in both."""
    address = _fact("dei:EntityAddressAddressLine1", None, "a")
    revenue = _fact(REVENUE, 331839000000.0, "b")
    ranked = structured_query_v1.rank_records(
        [address, revenue], "Microsoft top line", {}, 10)
    assert [r["concept_name"] for _, r in ranked] == [REVENUE,
                                                      "dei:EntityAddressAddressLine1"]


def test_a_fact_with_no_number_is_still_returned_when_nothing_else_matches(no_store):
    """It sorts last rather than being dropped. Deleting evidence to fix a
    ranking fault would lose the only answer some questions have."""
    address = _fact("dei:EntityAddressAddressLine1", None, "a")
    ranked = structured_query_v1.rank_records(
        [address], "Microsoft address line", {}, 10)
    assert len(ranked) == 1
