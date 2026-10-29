import unittest
from src.segment import split_sentences, prefilter
from src.entity_annotation import annotate_mentions


class SourceBoundary(unittest.TestCase):
    def test_us_restrictions_starts_a_new_sentence(self):
        text = "We source components in the U.S. Restrictions on trade can affect supply."
        self.assertEqual([text[a:b] for a,b in split_sentences(text)],
                         ["We source components in the U.S.", "Restrictions on trade can affect supply."])

    def test_named_us_department_remains_one_sentence(self):
        text = "The U.S. Department of Commerce initiated an investigation."
        self.assertEqual([text[a:b] for a,b in split_sentences(text)], [text])

    def test_reference_with_change_assertion_is_not_automatically_dropped(self):
        text = "Except for the risks incorporated by reference herein, there have been no material changes to our risk factors."
        self.assertIsNone(prefilter(text, 4))


class EntityMentions(unittest.TestCase):
    accession = "0000320193-25-000079"

    def catalog(self, **changes):
        row = {"entity_id":"apple-europe-segment", "entity_type":"segment", "issuer_cik":"0000320193",
               "name":"Europe", "aliases":[], "accessions":[self.accession], "review_status":"reviewed"}
        return [row | changes]

    def run_mention(self, text, mention, catalog=None):
        return annotate_mentions(text, [mention], issuer_cik="0000320193", accession=self.accession,
                                 catalog=self.catalog() if catalog is None else catalog)

    def mention(self, text="The Company's reportable segments include Europe.", **changes):
        return {"mention_text":"Europe", "entity_type":"segment", "reference_kind":"named", "evidence_quote":text} | changes

    def test_reviewed_catalog_and_explicit_type_match(self):
        text = "The Company's reportable segments include Europe."
        out=self.run_mention(text,self.mention(text))
        self.assertEqual(out["related_segment_ids"],["apple-europe-segment"])
        self.assertEqual(out["graph_relations_created"],0)

    def test_location_does_not_become_segment(self):
        text="Restrictions in Europe may affect trade."
        out=self.run_mention(text,self.mention(text))
        self.assertEqual(out["related_segment_ids"],[])
        self.assertEqual(out["entity_mentions"][0]["match_status"],"needs_type_evidence_review")

    def test_no_catalog_is_explicit(self):
        text="The Company's reportable segments include Europe."
        out=self.run_mention(text,self.mention(text),[])
        self.assertEqual(out["entity_mentions"][0]["match_status"],"needs_catalog")
        self.assertIsNone(out["entity_mentions"][0]["matched_entity_id"])

    def test_generic_subsidiaries_do_not_get_an_id(self):
        text="Privacy laws apply to our international subsidiaries."
        mention=self.mention(text,mention_text="international subsidiaries",entity_type="subsidiary",reference_kind="generic")
        out=self.run_mention(text,mention,[])
        self.assertEqual(out["entity_mentions"][0]["match_status"],"generic_reference")
        self.assertEqual(out["related_subsidiary_ids"],[])

    def test_invented_name_is_flagged(self):
        text="We sell Windows products."
        out=self.run_mention(text,self.mention(text,mention_text="Microsoft Ireland",entity_type="subsidiary"),[])
        self.assertTrue(out["entity_review_flags"])
        self.assertEqual(out["related_subsidiary_ids"],[])

    def test_unreviewed_catalog_stays_candidate(self):
        text="The Company's reportable segments include Europe."
        out=self.run_mention(text,self.mention(text),self.catalog(review_status="needs_review"))
        self.assertEqual(out["related_segment_ids"],[])
        self.assertEqual(out["entity_mentions"][0]["match_status"],"candidate_match_needs_review")

    def test_other_filing_or_issuer_cannot_match(self):
        text="The Company's reportable segments include Europe."
        for change in [{"accessions":["other"]},{"issuer_cik":"0000789019"}]:
            out=self.run_mention(text,self.mention(text),self.catalog(**change))
            self.assertEqual(out["related_segment_ids"],[])


if __name__ == "__main__": unittest.main()
