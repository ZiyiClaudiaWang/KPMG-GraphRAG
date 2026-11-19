import unittest
from src.segment import (split_sentences, prefilter, is_bullet, merge_list, is_period_heading, is_heading_like,
                         is_category, is_colon_heading, mark_risk_summary, segment_section)
from src.entity_annotation import annotate_mentions
from src.clean import drop_rule, should_join
from src.risk_factors import assign, category_key, is_lead_sentence
from src.common import source_text, unit_id


class SourceBoundary(unittest.TestCase):
    def test_us_restrictions_starts_a_new_sentence(self):
        text = "We source components in the U.S. Restrictions on trade can affect supply."
        self.assertEqual([text[a:b] for a,b in split_sentences(text)],
                         ["We source components in the U.S.", "Restrictions on trade can affect supply."])

    def test_us_cultural_starts_a_new_sentence(self):
        # README Remaining issues 2.9 (Take-Two)
        text = "Our hit products may not sell as well as they do in the U.S. Cultural differences may also require changes."
        self.assertEqual(len(split_sentences(text)), 2)

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

    def test_microsoft_item_links_are_removed(self):
        # README Remaining issues 2.4 (old): page-footer navigation, not a heading or category
        for text in ("Item 1B, 1C", "Item 9B, 9C, 10, 11", "Item 3, 4"):
            self.assertEqual(drop_rule(text, set()), "toc_link", text)
        self.assertEqual(drop_rule("Item 7", set()), "page_label")
        self.assertIsNone(drop_rule("Item 1A, Risk Factors, describes these risks.", set()))

    def test_table_cells_and_footnote_markers_are_table_residue(self):
        # README Remaining issues 2.3 and 2.4 (old)
        for text in ("Chief Executive Officer |", "Amy E. Hood |", "Programs (3) (4) |", "Shares", "(a)", "(1)"):
            self.assertFalse(is_heading_like(text), text)
            self.assertEqual(prefilter(text, 4), "table_residue", text)
        self.assertIsNone(prefilter("Mr. Cordano holds a BS from the University of Colorado, Boulder. |", 4))

    def test_sentence_cut_after_a_dangling_word_is_joined(self):
        # Take-Two: "...were blocked by the" / page 26 / "Sixth Circuit Court of Appeals."
        self.assertTrue(self.join("efforts to reinstate net neutrality regulations in the U.S. were blocked by the",
                                  "Sixth Circuit Court of Appeals."))
        # McKesson: "...Prescription Technology Solutions, and" / "Medical-Surgical Solutions."
        self.assertTrue(self.join("we have four reportable segments: North American Pharmaceutical, Prescription "
                                  "Technology Solutions, and", "Medical-Surgical Solutions."))

    def test_page_starting_with_an_amount_or_a_parenthesis_is_joined(self):
        # README Remaining issues 2.1: Medtronic and Nike sentences cut by a page break
        self.assertTrue(self.join("Notes due 2028. The Company used the net proceeds to repay",
                                  "€500 million of Medtronic Luxco’s 2.625% Senior Notes."))
        self.assertTrue(self.join("its fair value exceeded its carrying value, by approximately 12%. As of July 31, 2026,",
                                  "$19.7 billion of goodwill was allocated to the Medical Surgical reporting unit."))
        self.assertTrue(self.join("our information technology systems, including the Internet and third-party services",
                                  '("Information Technology Systems"), may not be sufficient.'))
        # a table caption after a heading-like line stays its own paragraph
        self.assertFalse(self.join("Three Months Ended July 31, 2026 Compared to Three Months Ended July 31, 2025",
                                   "(Dollars and Amounts in Thousands)"))

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


class RiskCategories(unittest.TestCase):
    """segment.py: Item 1A category headings and the risk factor summary."""

    def test_lowercase_risks_related_to_is_a_category(self):
        # TTWO writes its categories in sentence case
        self.assertTrue(is_category("Risks relating to our business and industry"))
        self.assertTrue(is_category("Business Risks"))
        self.assertFalse(is_category("Competition in the technology sector"))     # MSFT sub-heading

    def test_summary_is_flagged_until_a_category_repeats(self):
        def unit(role, text, rule=None):
            return {"role": role, "text": text, "prefilter": rule, "risk_summary": False}
        units = [unit("category_heading", "Risk Factor Summary", "heading"),
                 unit("category_heading", "Risks Related to Our Business", "heading"),
                 unit("body", "volatility in average selling prices;"),
                 unit("category_heading", "Risks Related to Our Business", "heading"),
                 unit("body", "Volatility in average selling prices may harm our results.")]
        mark_risk_summary(units)
        self.assertEqual([u["risk_summary"] for u in units], [True, True, True, False, False])
        self.assertIsNone(units[2]["prefilter"])                  # still sent to the LLM

    def test_summary_without_a_repeat_is_left_alone(self):
        units = [{"role": "category_heading", "text": "Summary of Risk Factors", "prefilter": "heading",
                  "risk_summary": False},
                 {"role": "body", "text": "Our industry is highly competitive.", "prefilter": None,
                  "risk_summary": False}]
        mark_risk_summary(units)
        self.assertFalse(units[1]["risk_summary"])

    def test_sentences_take_the_themes_of_their_category(self):
        def unit(n, role, text, rule_heading=None):
            return {"unit_id": f"u{n}", "accession_number": "A", "ticker": "T", "form": "10-K",
                    "section_key": "item-1a", "section_type": "risk_factors", "role": role, "text": text,
                    "rule_heading": rule_heading, "risk_summary": False}
        units = [unit(1, "body", "The following risks could affect us."),
                 unit(2, "category_heading", "Risks Related to Intellectual Property and Litigation"),
                 unit(3, "body", "We may be sued for infringement.", "high"),
                 unit(4, "body", "Such claims are costly."),
                 unit(5, "category_heading", "INDEX TO RISK FACTORS")]
        mapping = {category_key("Risks related to intellectual property and litigation"):
                   ["intellectual_property", "legal_regulatory"],
                   category_key("Index to Risk Factors"): []}
        categories, assignments = assign(units, {}, mapping)
        self.assertEqual([c["map_status"] for c in categories], ["mapped", "not_a_theme"])
        by_id = {a["unit_id"]: a for a in assignments}
        self.assertEqual(by_id["u1"]["risk_ids"], [])                    # before any category
        self.assertEqual(by_id["u4"]["risk_ids"], ["intellectual_property", "legal_regulatory"])
        self.assertEqual(by_id["u4"]["risk_lead_unit_id"], "u3")           # unit ids (#45)
        self.assertEqual(by_id["u4"]["risk_category_id"], "u2")
        self.assertEqual(categories[0]["risk_category_id"], "u2")
        self.assertTrue(by_id["u3"]["risk_lead_sentence"])


class SourceMatching(unittest.TestCase):
    """common.py: one rule for comparing a unit with the source (#35 review, point 2)."""

    def test_whitespace_reads_as_one_space(self):
        content = "Supply\nproblems\u00a0could raise costs. Next sentence."
        end = content.index(" Next")
        self.assertEqual(source_text(content, [[0, end]]), "Supply problems could raise costs.")

    def test_page_break_fragments_rebuild_the_sentence(self):
        content = "Supply\nproblems could raise costs. Delays may\n\n12\n\nfollow."
        second = content.index("follow.")
        joined = [[35, 45], [second, second + 7]]                  # the page number is left out
        self.assertEqual(source_text(content, joined), "Delays may follow.")

    def test_repaired_label_counts_as_model_judgement(self):
        unit = {"unit_id": "x", "rule_heading": None}
        for source in ("llm", "llm_repair"):
            self.assertTrue(is_lead_sentence(unit, {"x": {"source": source, "is_risk_heading": True}}))
        self.assertTrue(is_lead_sentence({"unit_id": "y", "rule_heading": "high"}, {}))


class UnitIdentity(unittest.TestCase):
    """#45: a unit's id comes from its filing, section and source spans, not its text."""

    def test_same_spans_same_id(self):
        self.assertEqual(unit_id("A", "item-1a", [[0, 10], [14, 20]]),
                         unit_id("A", "item-1a", [(0, 10), (14, 20)]))

    def test_any_position_change_gives_a_new_id(self):
        base = unit_id("A", "item-1a", [[0, 10]])
        for other in (unit_id("A", "item-1a", [[0, 11]]), unit_id("A", "item-1a", [[0, 5], [6, 10]]),
                      unit_id("A", "item-7", [[0, 10]]), unit_id("B", "item-1a", [[0, 10]])):
            self.assertNotEqual(base, other)


class ColonHeadings(unittest.TestCase):
    """README Remaining issues 2.2 (old): short title-case lines ending in ":" are headings."""

    def test_which_lines_are_headings(self):
        for text in ("Executive Summary:", "OPERATIONS:", "Oncology & Multispecialty:",
                     "Sales and Marketing (in millions, except percentages):",
                     "Other Segment Expense, Segment Operating Profit, and Corporate Expenses, Net:"):
            self.assertTrue(is_colon_heading(text), text)
        for text in ("Our cost of revenues consists of:", "The breakdown of Revenues is as follows:",
                     "Our strategy is anchored in four key focus areas:", "Executive Summary"):
            self.assertFalse(is_colon_heading(text), text)

    def test_heading_names_the_units_below_unless_a_list_follows(self):
        def section(*paragraphs):
            text, out, pos = "", [], 0
            for p in paragraphs:
                out.append({"text": p, "start": pos, "pieces": [[pos, pos + len(p), pos]]})
                text += p + "\n\n"
                pos += len(p) + 2
            return {"accession_number": "A", "section_key": "part-ii-item-7", "ticker": "T", "form": "10-K",
                    "section_id": "s", "section_type": "mda", "processing_set_id": "p", "source_locator": None,
                    "part": "II", "item": "7", "original_title": "MD&A", "text": text[:-2], "paragraphs": out}
        cfg = {"min_words": 4, "short_name_max_chars": 60, "list_merge_max_chars": 600}
        units = segment_section(section("Executive Summary:", "Revenue grew in every segment this year."), cfg)
        self.assertEqual((units[0]["role"], units[0]["prefilter"]), ("heading", "heading"))
        self.assertEqual(units[1]["subheading"], "Executive Summary")
        units = segment_section(section("Key Products:", "• Widgets for industrial customers in Europe",
                                        "• Gadgets for retail customers in Asia"), cfg)
        self.assertNotEqual(units[0]["role"], "heading")                    # a list intro, not a heading
