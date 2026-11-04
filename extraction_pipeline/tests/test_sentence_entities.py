import unittest
from src.segment import split_sentences, prefilter, is_bullet, merge_list, is_period_heading, is_heading_like
from src.entity_annotation import annotate_mentions
from src.clean import drop_rule, should_join


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

    def test_bullet_without_space_is_a_bullet(self):
        # Most companies' cleaned text has no space after "•" (AAPL, MU, MDT, ...).
        self.assertTrue(is_bullet("•MacBook Pro"))
        self.assertTrue(is_bullet("• Mac mini"))
        self.assertTrue(is_bullet("o second-level item"))

    def test_words_and_numbers_are_not_bullets(self):
        self.assertFalse(is_bullet("our revenue grew"))
        self.assertFalse(is_bullet("other income"))
        self.assertFalse(is_bullet("-5% year over year"))

    def sentences(self, text):
        return [text[a:b] for a, b in split_sentences(text)]

    def test_opener_with_comma_after_inc_starts_a_new_sentence(self):
        # MSFT: "Additionally" was already an opener but did not match with its comma
        self.assertEqual(self.sentences("We acquired Activision Blizzard, Inc. Additionally, we expanded our gaming business."),
                         ["We acquired Activision Blizzard, Inc.", "Additionally, we expanded our gaming business."])

    def test_new_opener_after_us_starts_a_new_sentence(self):
        # MCK: "...in the U.S. During fiscal 2026..."
        self.assertEqual(self.sentences("Sales grew in the U.S. During fiscal 2026 we opened new centers."),
                         ["Sales grew in the U.S.", "During fiscal 2026 we opened new centers."])

    def test_item_reference_stays_one_sentence(self):
        # NKE: "Refer to Item 1A." / "Risk Factors." used to be cut apart
        text = "Refer to Item 1A. Risk Factors for more information."
        self.assertEqual(self.sentences(text), [text])
        # ...unless the next word starts a sentence
        self.assertEqual(self.sentences("See Item 1A. We also describe other risks."),
                         ["See Item 1A.", "We also describe other risks."])

    def test_note_reference_stays_one_sentence(self):
        text = ("See Item 8. Financial Statements, Notes to Consolidated Financial Statements, "
                "Note 9. Debt as well as Note 10. Commitments. We also lease equipment.")
        sentences = [text[s:e] for s, e in split_sentences(text)]
        self.assertEqual(len(sentences), 2)
        self.assertTrue(sentences[0].endswith("Note 9. Debt as well as Note 10. Commitments."))


    def test_non_us_does_not_end_a_sentence(self):
        text = "For fiscal 2026, non-U.S. NIKE Brand and Converse sales accounted for approximately 56% of revenues."
        self.assertEqual(len(split_sentences(text)), 1)

    def test_form_10k_still_ends_a_sentence(self):
        text = "See our Annual Report on Form 10-K. Additional risks may also affect us."
        self.assertEqual(len(split_sentences(text)), 2)

    def test_heading_with_a_period(self):
        for heading in ("Flexible Work.", "Zynga.", "2K.", "Community & Engagement.",
                        "Develop Robust Player Relationships."):
            self.assertTrue(is_period_heading(heading), heading)
        for sentence in ("Revenue increased.", "Player acquisition costs.", "Our revenue grew in all regions this year."):
            self.assertFalse(is_period_heading(sentence), sentence)


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


class PageBreaks(unittest.TestCase):
    """clean.py: page furniture removed, sentences cut by a page break joined back."""

    def join(self, before, after):
        return should_join(before, [[0, len(before)]], after)

    def test_page_furniture_is_removed(self):
        self.assertEqual(drop_rule("FINANCIAL REVIEW (Continued)", set()), "continued_header")
        self.assertEqual(drop_rule("FINANCIAL REVIEW (Concluded)", set()), "continued_header")
        self.assertEqual(drop_rule("Table of Contents | MD&A Index |", set()), "toc_link")
        self.assertIsNone(drop_rule("Fiscal Year 2026 Compared with Fiscal Year 2025", set()))

    def test_unaudited_label_is_removed(self):
        # McKesson 10-Q: "(UNAUDITED)" under the page header on every page
        self.assertEqual(drop_rule("(UNAUDITED)", set()), "unaudited_label")
        self.assertIsNone(drop_rule("The unaudited results include the following:", set()))

    def test_listed_table_notes_are_table_residue_not_headings(self):
        for note in ("NM - Not Meaningful.", "bp  -  Basis Point", "Shares that May"):
            self.assertFalse(is_heading_like(note), note)
            self.assertEqual(prefilter(note, 4), "table_residue")
        self.assertIsNone(drop_rule("NM - Not Meaningful", set()))   # clean keeps it
        self.assertIsNone(prefilter("Gross margin increased 40 bp - basis points - in the quarter.", 4))

    def test_table_header_piece_is_not_a_heading(self):
        # McKesson share-repurchase table header: "Number of" / "Dollar Value of" / "Under the"
        for text in ("Number of", "Dollar Value of", "Under the"):
            self.assertFalse(is_heading_like(text), text)
        self.assertTrue(is_heading_like("North American Pharmaceutical"))

    def test_sentence_cut_after_a_dangling_word_is_joined(self):
        # Take-Two: "...were blocked by the" / page 26 / "Sixth Circuit Court of Appeals."
        self.assertTrue(self.join("efforts to reinstate net neutrality regulations in the U.S. were blocked by the",
                                  "Sixth Circuit Court of Appeals."))
        # McKesson: "...Prescription Technology Solutions, and" / "Medical-Surgical Solutions."
        self.assertTrue(self.join("we have four reportable segments: North American Pharmaceutical, Prescription "
                                  "Technology Solutions, and", "Medical-Surgical Solutions."))

    def test_list_item_ending_in_semicolon_and_is_not_joined(self):
        self.assertFalse(self.join("lower warehousing and logistics costs, primarily due to channel mix; and",
                                   "Lower other costs."))


class ListRule(unittest.TestCase):
    """merge_list(): which intro + bullet lists become one list_group unit."""
    cfg = {"short_name_max_chars": 60, "list_merge_max_chars": 600}

    def bullets(self, *texts):
        return [{"text": t} for t in texts]

    def test_short_names_merge(self):
        # Apple's product launches: names only make sense together.
        self.assertTrue(merge_list("First Quarter 2025:", self.bullets("•MacBook Pro", "•Mac mini", "•iMac"), self.cfg))

    def test_short_fragment_list_merges(self):
        # Nike's gross-margin drivers: three fragments under one "due to the following:".
        self.assertTrue(merge_list(
            "Gross margin increased 20 basis points due to the following:",
            self.bullets("•Lower warehousing and logistics costs, primarily due to channel mix;",
                         "•Favorable changes in net foreign currency exchange rates; and",
                         "•Lower other costs."), self.cfg))

    def test_long_fragment_list_splits(self):
        # Micron's international risks: each item is its own risk, too long as one unit.
        items = ["•restrictions on sales of goods or services to one or more of our significant foreign customers;"] * 8
        self.assertFalse(merge_list("Our international operations are subject to a number of risks, including:",
                                    self.bullets(*items), self.cfg))

    def test_sentence_list_splits(self):
        # Every item stands alone, however short the list.
        self.assertFalse(merge_list("Results:", self.bullets("•Revenue increased 5%.", "•Margins improved."), self.cfg))


if __name__ == "__main__": unittest.main()

