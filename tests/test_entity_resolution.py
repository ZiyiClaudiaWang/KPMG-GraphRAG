from __future__ import annotations

import csv
from pathlib import Path

import pytest

from graphrag.entity_resolution import (
    AMBIGUOUS,
    EX21,
    MATCH,
    NO_MATCH,
    Provenance,
    TEXT,
    name_similarity,
    normalize_name,
    resolve_subsidiary_names,
    resolve_subsidiary_names_with_provenance,
    split_legal_form,
)

ROOT = Path(__file__).resolve().parents[1]


def test_normalize_name_is_case_and_punctuation_insensitive():
    assert normalize_name("J.P. Morgan Securities PLC") == "j p morgan securities plc"
    assert normalize_name("  Apple   Asia, LLC ") == "apple asia llc"


def test_equivalent_legal_form_abbreviation_matches():
    out = resolve_subsidiary_names(
        "JPMorgan Chase Bank, N.A.",
        "JPMorgan Chase Bank, National Association",
    )
    assert out.decision == MATCH


def test_conflicting_explicit_legal_forms_block_merge():
    out = resolve_subsidiary_names("Apple Asia LLC", "Apple Asia Limited")
    assert out.decision == NO_MATCH
    assert "legal forms" in out.reason


def test_missing_legal_form_is_ambiguous_without_context():
    out = resolve_subsidiary_names("LinkedIn", "LinkedIn Corporation")
    assert out.decision == AMBIGUOUS


def test_filing_defined_alias_overrides_string_ambiguity():
    aliases = {"J.P. Morgan Securities": "J.P. Morgan Securities LLC"}
    out = resolve_subsidiary_names(
        "J.P. Morgan Securities",
        "J.P. Morgan Securities LLC",
        defined_aliases=aliases,
    )
    assert out.decision == MATCH


def test_filing_defined_alias_does_not_match_wrong_legal_entity():
    aliases = {"J.P. Morgan Securities": "J.P. Morgan Securities LLC"}
    out = resolve_subsidiary_names(
        "J.P. Morgan Securities",
        "J.P. Morgan Securities plc",
        defined_aliases=aliases,
    )
    assert out.decision != MATCH


def test_explicit_jurisdiction_conflict_blocks_merge():
    out = resolve_subsidiary_names(
        "Example Holdings",
        "Example Holdings",
        jurisdiction_a="Delaware",
        jurisdiction_b="Hong Kong",
    )
    assert out.decision == NO_MATCH
    assert "jurisdictions" in out.reason


def test_dissimilar_names_do_not_match():
    out = resolve_subsidiary_names("Apple Japan, Inc.", "iTunes K.K.")
    assert out.decision == NO_MATCH


def test_similarity_is_bounded():
    assert 0 <= name_similarity("Apple Operations", "Apple Operations Limited") <= 1


def test_split_legal_form_canonicalizes_na():
    core, form = split_legal_form("JPMorgan Chase Bank, N.A.")
    assert core == "jpmorgan chase bank"
    assert form == "national association"


def test_bad_name_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        resolve_subsidiary_names("", "Apple Inc.")


def test_bad_thresholds_rejected():
    with pytest.raises(ValueError, match="thresholds"):
        resolve_subsidiary_names("A", "B", low_similarity=0.9, high_similarity=0.5)


def test_name_only_baseline_has_no_false_positive_on_50_pair_fixture():
    """The baseline is intentionally conservative: do not merge a known 'no'."""
    rows = list(csv.DictReader((ROOT / "toy_graph" / "50_subsidiary_pairs.csv").open(encoding="utf-8-sig")))
    false_positives = []
    for row in rows:
        result = resolve_subsidiary_names(row["name_a"], row["name_b"])
        if result.decision == MATCH and row["label"] == "no":
            false_positives.append(row["pair_id"])
    assert false_positives == []


# --- Exhibit 21 succession: the second pass ----------------------------------


def _msft(year: int, siblings: tuple[str, ...] = ()) -> Provenance:
    return Provenance("MSFT", EX21, year, siblings)


def test_succession_settles_a_cross_year_rename():
    out = resolve_subsidiary_names_with_provenance(
        "Microsoft Global Finance",
        "Microsoft Global Finance Unlimited Company",
        provenance_a=_msft(2024),
        provenance_b=_msft(2025),
    )
    assert out.decision == MATCH
    assert "FY2024 and FY2025" in out.reason


def test_two_names_in_the_same_exhibit_are_two_companies():
    """J.P. Morgan Securities LLC and plc are co-listed, so they stay unresolved."""
    out = resolve_subsidiary_names_with_provenance(
        "J.P. Morgan Securities LLC",
        "J.P. Morgan Securities plc",
        provenance_a=Provenance("JPM", EX21, 2025),
        provenance_b=Provenance("JPM", EX21, 2025),
    )
    assert out.decision != MATCH


def test_prose_mention_is_not_succession():
    """LinkedIn in the 10-K text is a business line, not last year's entry."""
    out = resolve_subsidiary_names_with_provenance(
        "LinkedIn",
        "LinkedIn Corporation",
        provenance_a=Provenance("MSFT", TEXT, 2025),
        provenance_b=_msft(2025),
    )
    assert out.decision == AMBIGUOUS


def test_different_registrants_are_not_succession():
    out = resolve_subsidiary_names_with_provenance(
        "Atlantic Industries",
        "Atlantic Industries Limited",
        provenance_a=Provenance("KO", EX21, 2024),
        provenance_b=Provenance("PEP", EX21, 2025),
    )
    assert out.decision == AMBIGUOUS


def test_a_shared_core_in_the_later_filing_blocks_the_merge():
    """If two of this year's names share last year's core, neither is the successor."""
    out = resolve_subsidiary_names_with_provenance(
        "Acme Holdings",
        "Acme Holdings Limited",
        provenance_a=_msft(2024, ("Acme Holdings",)),
        provenance_b=_msft(2025, ("Acme Holdings Limited", "Acme Holdings Inc.")),
    )
    assert out.decision == AMBIGUOUS


def test_succession_never_revisits_a_refusal():
    """The name rules already said no; evidence about years must not overturn it."""
    out = resolve_subsidiary_names_with_provenance(
        "Apple Operations Limited",
        "Apple Operations GmbH",
        provenance_a=Provenance("AAPL", EX21, 2018),
        provenance_b=Provenance("AAPL", EX21, 2019),
    )
    assert out.decision == NO_MATCH


def test_second_pass_raises_recall_on_the_fixture_without_a_false_merge():
    import sys

    sys.path.insert(0, str(ROOT))
    from tools.evaluate_entity_resolution import evaluate

    pairs = ROOT / "toy_graph" / "50_subsidiary_pairs.csv"
    baseline = evaluate(pairs)
    second = evaluate(pairs, second_pass=True)

    assert baseline["fp"] == 0 and second["fp"] == 0
    assert second["tp"] > baseline["tp"]
    assert second["recall"] > baseline["recall"]
    assert second["precision"] == baseline["precision"] == 1.0
