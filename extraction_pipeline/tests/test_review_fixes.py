"""Fixes from the review before the paid runs (27 Nov 2026): page-break join after a closing
parenthesis, dictionary hits inside an 8b product mention, a reviewed segment name over the skip
list, step 2b answers compared by id, and room for long 5b replies."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from src import clean as C
from src.entities import catalog as entity_catalog
from src.entities.annotation import annotate_mentions
from src.risk.themes import answer_room

MSFT, ACC = "0000789019", "0000789019-26-000001"
CATALOG = [{"entity_id": "linkedin", "entity_type": "subsidiary", "issuer_cik": MSFT, "name": "LinkedIn Corporation",
            "aliases": [], "accessions": [ACC], "prior_year_only_accessions": [], "review_status": "needs_review",
            "source": "exhibit_21"}]


def boundary(prev: str, nxt: str) -> str:
    raw = prev + "\n\n" + nxt
    return C.boundary_class(raw, [[0, len(prev)]], nxt)


class CloseThenLowerCase(unittest.TestCase):
    def test_closing_parenthesis_then_lower_case_joins(self):
        self.assertEqual(boundary("We seek to link compensation (including annual changes in compensation)",
                                  "to our overall and business unit performance."), "join_close_lowercase")

    def test_closing_quote_then_lower_case_joins(self):
        self.assertEqual(boundary("the enforceability of similar exclusive forum provisions in other companies’",
                                  "organizational documents has been challenged"), "join_close_lowercase")

    def test_sentence_end_before_the_parenthesis_stays(self):
        self.assertEqual(boundary("Revenue grew in every segment this year (see Note 3.)",
                                  "and margins were flat."), "sentence_end")

    def test_capital_next_stays(self):
        self.assertEqual(boundary("We seek to link compensation (including annual changes in compensation)",
                                  "Our overall results improved."), "sentence_end")


class TypedMentionsBlockDictionary(unittest.TestCase):
    TEXT = "Subscriptions to LinkedIn Learning grew in Japan."

    def test_product_mention_is_not_linked(self):
        typed = [{"mention_text": "LinkedIn Learning", "entity_type": "product"}]
        out = annotate_mentions(self.TEXT, [], issuer_cik=MSFT, accession=ACC, catalog=CATALOG,
                                typed_mentions=typed)
        m = out["entity_mentions"][0]
        self.assertEqual((m["found_by"], m["match_status"]), (["dictionary"], "typed_as_product"))
        self.assertEqual(out["related_subsidiary_ids"], [])

    def test_without_8b_types_the_dictionary_links(self):
        out = annotate_mentions(self.TEXT, [], issuer_cik=MSFT, accession=ACC, catalog=CATALOG)
        self.assertEqual(out["related_subsidiary_ids"], ["linkedin"])

    def test_organization_mention_does_not_block(self):
        typed = [{"mention_text": "LinkedIn Learning", "entity_type": "organization"}]
        out = annotate_mentions(self.TEXT, [], issuer_cik=MSFT, accession=ACC, catalog=CATALOG,
                                typed_mentions=typed)
        self.assertEqual(out["related_subsidiary_ids"], ["linkedin"])


class ReviewedSegmentNameBeatsSkip(unittest.TestCase):
    def test_googl_other_bets(self):
        cik, acc = "0001652044", "0001652044-26-000018"
        original = entity_catalog.filings
        entity_catalog.filings = lambda: [{"ticker": "GOOGL", "cik": cik, "accession": acc}]
        try:
            entries = entity_catalog.segment_entries([(cik, acc, "us-gaap:AllOtherSegmentsMember"),
                                                      (cik, acc, "goog:GoogleCloudMember")])
        finally:
            entity_catalog.filings = original
        self.assertIn("Other Bets", {e["name"] for e in entries})

    def test_skip_still_applies_without_a_reviewed_name(self):
        cik, acc = "0000320193", "0000320193-25-000079"
        original = entity_catalog.filings
        entity_catalog.filings = lambda: [{"ticker": "AAPL", "cik": cik, "accession": acc}]
        try:
            entries = entity_catalog.segment_entries([(cik, acc, "us-gaap:AllOtherSegmentsMember")])
        finally:
            entity_catalog.filings = original
        self.assertEqual(entries, [])


class PageBreakAnswersById(unittest.TestCase):
    def test_count_difference_does_not_hide_an_unanswered_boundary(self):
        from src.checks import report
        tmp = Path(tempfile.mkdtemp(prefix="pb-report-"))
        old = os.environ.get("PIPELINE_DATA_DIR")
        os.environ["PIPELINE_DATA_DIR"] = str(tmp)
        try:
            (tmp / "checks" / "page_breaks").mkdir(parents=True)
            (tmp / "interim").mkdir()
            (tmp / "checks" / "page_breaks" / "page_break_candidates.jsonl").write_text(
                "\n".join(json.dumps({"boundary_id": b}) for b in ("a", "b")) + "\n")
            (tmp / "interim" / "page_break_decisions.jsonl").write_text(
                "\n".join(json.dumps({"boundary_id": b, "same_sentence": False}) for b in ("a", "x", "y")) + "\n")
            m = report.page_breaks_numbers()[0]
        finally:
            if old is None:
                os.environ.pop("PIPELINE_DATA_DIR")
            else:
                os.environ["PIPELINE_DATA_DIR"] = old
        self.assertEqual((m["not_answered"], m["decisions_not_a_candidate"]), (1, 2))


class RiskThemeAnswerRoom(unittest.TestCase):
    def test_room_grows_with_the_risk_factor(self):
        cfg = {"answer_tokens": 300}
        self.assertEqual(answer_room(5, cfg), 300)
        self.assertEqual(answer_room(85, cfg), 780)


if __name__ == "__main__":
    unittest.main()
