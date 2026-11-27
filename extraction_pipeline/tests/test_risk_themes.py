"""Step 5b requests and the risk taxonomy files. No network, no API."""

import os
import unittest

from src.llm import cost as llm_cost
from src.risk import factors as risk_factors, taxonomy as risk_taxonomy, themes as risk_themes
from src.risk.taxonomy import category_key


def unit(n, acc, text, lead=None, **kw):
    return {"unit_id": f"{acc}-u{n}", "accession_number": acc, "ticker": "TST", "form": "10-K",
            "section_key": "part-i-item-1a", "section_type": "risk_factors", "role": "body", "text": text,
            "prefilter": None, "risk_summary": False, "lead_source": None, "lead_unit_id": lead, **kw}


def factor(acc, body):
    lead = unit(1, acc, "Competition could harm our results.", lead_source="html")
    return [lead] + [unit(k + 2, acc, t, lead=lead["unit_id"]) for k, t in enumerate(body)]


class TestRiskFactors(unittest.TestCase):
    def test_lead_and_units_under_it(self):
        units = (factor("A1", ["Rivals cut prices.", "See Note 5."])
                 + [unit(8, "A1", "Legal Risks", role="heading", heading_level=1, prefilter="heading"),
                    unit(9, "A1", "Under no lead."), unit(10, "A1", "Summary.", risk_summary=True,
                                                          lead_source="html")])
        units[2]["prefilter"] = "cross_reference"
        [rf] = risk_factors.risk_factors(units)
        self.assertEqual(rf["risk_factor_id"], "A1-u1")
        self.assertEqual([u["unit_id"] for u in rf["units"]], ["A1-u1", "A1-u2"])   # not the cross reference
        self.assertEqual([u["text"] for u in risk_themes.shown(rf, 80)],          # no cross reference
                         ["Competition could harm our results.", "Rivals cut prices."])

    def test_members_list_intros_and_sub_headings_not_other_not_a_claim(self):
        """Members: the lead, units step 7 labels, list intros, sub-headings. Tables, definitions,
        cross references and too_short units stay out, and do not end the risk factor."""
        units = factor("A1", ["Our suppliers include:", "a table row", "Rivals cut prices.", "Note 5.", "x"])
        units[1]["prefilter"], units[2]["prefilter"] = "list_intro", "table_residue"
        units[4]["prefilter"], units[5]["prefilter"] = "definition", "too_short"
        units.insert(4, unit(9, "A1", "Pricing", role="heading", heading_level=2, prefilter="heading"))
        [rf] = risk_factors.risk_factors(units)
        self.assertEqual([u["unit_id"] for u in rf["units"]], ["A1-u1", "A1-u2", "A1-u4", "A1-u9"])
        self.assertEqual([u["unit_id"] for u in risk_themes.shown(rf, 80)], ["A1-u1", "A1-u2", "A1-u4", "A1-u9"])
        _, assignments = risk_factors.assign(units, {})
        risk_themes.apply(assignments, risk_factors.factor_rows([rf]), {})
        status = {a["unit_id"]: a["risk_theme_status"] for a in assignments}
        self.assertEqual(status["A1-u3"], "not_a_claim")
        self.assertEqual(status["A1-u4"], "not_classified")

    def test_sub_headings_below_a_lead_stay_inside(self):
        """Microsoft: category (1) > bold lead > italic sub-topics (2) > text; the next lead is a sibling."""
        def h(n, text, level):
            return unit(n, "A1", text, role="heading", heading_level=level, prefilter="heading")
        units = [h(1, "STRATEGIC AND COMPETITIVE RISKS", 1),
                 unit(2, "A1", "We face intense competition.", lead_source="html"),
                 h(3, "Competition in the technology sector", 2), unit(4, "A1", "Rivals are large."),
                 h(5, "Business model competition", 2), unit(6, "A1", "Models shift."),
                 unit(7, "A1", "Our focus on AI may not succeed.", lead_source="html"),
                 h(8, "AI investments", 2), unit(9, "A1", "Costs are high."),
                 h(10, "OPERATIONAL RISKS", 1), unit(11, "A1", "Text under no lead.")]
        a, b = risk_factors.risk_factors(units)
        self.assertEqual([u["unit_id"] for u in a["units"]], [f"A1-u{n}" for n in (2, 3, 4, 5, 6)])
        self.assertEqual([u["unit_id"] for u in b["units"]], [f"A1-u{n}" for n in (7, 8, 9)])
        self.assertIn("Competition in the technology sector", [u["text"] for u in risk_themes.shown(a, 80)])

    def test_heading_at_the_lead_level_ends_it(self):
        """Level-2 group headings with leads under them: the next level-2 heading ends the risk factor."""
        def h(n, text, level):
            return unit(n, "A1", text, role="heading", heading_level=level, prefilter="heading")
        units = [h(1, "Business Risks", 1), h(2, "Risks Related to Our Products", 2),
                 unit(3, "A1", "Demand may fall.", lead_source="html"), unit(4, "A1", "Prices drop."),
                 h(5, "Risks Related to Our Stock", 2), unit(6, "A1", "Intro under no lead.")]
        [rf] = risk_factors.risk_factors(units)
        self.assertEqual([u["unit_id"] for u in rf["units"]], ["A1-u3", "A1-u4"])

    def test_identical_risk_text_shares_one_request(self):
        cfg = llm_cost.step_cfg("risk_themes")
        units = factor("A1", ["Rivals cut prices."]) + factor("A2", ["Rivals cut prices."]) \
            + factor("A3", ["Rivals cut prices sharply."])
        plans = risk_themes.plans(risk_factors.risk_factors(units), cfg)
        self.assertEqual(len(plans), 3)
        self.assertEqual(plans[0]["request_hash"], plans[1]["request_hash"])
        self.assertNotEqual(plans[0]["request_hash"], plans[2]["request_hash"])
        self.assertEqual(len(risk_themes.unique(plans)), 2)
        self.assertNotIn("A1", plans[0]["request"]["messages"][1]["content"])     # no ids in the request
        system = plans[0]["request"]["messages"][0]["content"]
        self.assertIn("artificial_intelligence:", system)
        self.assertNotIn("strategy_market", system)                              # themes only, never groups

    def test_answer_drops_bad_parts(self):
        cfg = llm_cost.step_cfg("risk_themes")
        [plan] = risk_themes.plans(risk_factors.risk_factors(factor("A1", ["Rivals cut prices.", "Costs rise."])),
                                   cfg)
        content = ('{"result": {"primary_theme": "competition", "secondary_themes": ['
                   '{"theme_id": "costs_pricing_margins", "sentence_ids": ["s3", "s9"]},'
                   '{"theme_id": "competition", "sentence_ids": ["s1"]},'
                   '{"theme_id": "currency", "sentence_ids": ["s9"]},'
                   '{"theme_id": "tax", "sentence_ids": ["s2"]},'
                   '{"theme_id": "macroeconomic", "sentence_ids": ["s2"]}]}}')
        record = {"raw_response": {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}}
        problems, notes, result = risk_themes.answer(plan, record)
        self.assertEqual(problems, [])
        self.assertEqual(result["secondary_themes"], [{"theme_id": "costs_pricing_margins", "unit_ids": ["A1-u3"]},
                                                      {"theme_id": "tax", "unit_ids": ["A1-u2"]}])
        self.assertEqual(len(notes), 5)       # s9 dropped (twice), repeat, currency left empty, beyond two


class TestTaxonomyFiles(unittest.TestCase):
    def setUp(self):                         # test_pipeline points the map at its fixture
        self.saved = os.environ.pop("PIPELINE_RISK_CATEGORY_MAP", None)

    def tearDown(self):
        if self.saved is not None:
            os.environ["PIPELINE_RISK_CATEGORY_MAP"] = self.saved

    def test_category_map_is_valid_and_covers_the_new_texts(self):
        mapping = risk_taxonomy.category_map()           # stops on an unknown group or a repeated text
        for text in ["Business and Industry Risks", "Operating Risks",
                     "Risks Related to Laws, Regulations, and Policies", "Risks Related to Ownership of Our Stock",
                     "Risks Related to Ownership of our Stock", "Risks Specific to our Company",
                     "BUSINESS AND STRATEGIC RISKS", "LEGAL AND COMPLIANCE RISKS",
                     "Legal, Tax, Regulatory, Compliance, Reputational and Other Risks", "Strategic Risks"]:
            self.assertIn(category_key(text), mapping, text)

    def test_themes_belong_to_known_groups(self):
        themes = risk_taxonomy.themes()
        self.assertEqual(len(themes), 32)
        self.assertEqual(len(risk_taxonomy.groups()), 7)
        self.assertNotIn(risk_taxonomy.UNKNOWN, themes)


if __name__ == "__main__":
    unittest.main()


class TestApply(unittest.TestCase):
    """Step 5b's answers on step 5's units (what step 9 puts on the Claims)."""

    def setUp(self):
        units = factor("A1", ["Rivals cut prices.", "Costs rise."]) + [
            unit(8, "A1", "Legal Risks", role="heading", heading_level=1, prefilter="heading"),
            unit(9, "A1", "Under no lead.")]
        _, self.assignments = risk_factors.assign(units, {})
        self.factors = risk_factors.factor_rows(risk_factors.risk_factors(units))
        theme = next(iter(risk_taxonomy.themes()))
        self.answer = {"risk_factor_id": "A1-u1", "unit_ids": ["A1-u1", "A1-u2", "A1-u3"], "primary_theme": theme,
                       "secondary_themes": [], "model": "m", "taxonomy_version": "v"}

    def status(self, results):
        risk_themes.apply(self.assignments, self.factors, results)
        return {a["unit_id"]: a["risk_theme_status"] for a in self.assignments}

    def test_answer_on_every_unit_of_the_risk_factor(self):
        status = self.status({"A1-u1": self.answer})
        self.assertEqual(status, {"A1-u1": "classified", "A1-u2": "classified", "A1-u3": "classified",
                                  "A1-u9": "no_risk_factor"})

    def test_answer_for_other_units_is_not_used(self):           # step 4 or 5 changed the risk factor since
        status = self.status({"A1-u1": dict(self.answer, unit_ids=["A1-u1", "A1-u2"])})
        self.assertEqual(status["A1-u1"], "not_classified")
