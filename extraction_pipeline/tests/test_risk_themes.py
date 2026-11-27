"""Step 4c requests and the risk taxonomy files. No network, no API."""

import os
import unittest

from src import llm_cost, risk_taxonomy, risk_themes
from src.risk_taxonomy import category_key


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
                 + [unit(9, "A1", "Under no lead."), unit(10, "A1", "Summary.", risk_summary=True,
                                                          lead_source="html")])
        units[2]["prefilter"] = "cross_reference"
        [rf] = risk_themes.risk_factors(units)
        self.assertEqual(rf["risk_factor_id"], "A1-u1")
        self.assertEqual([u["unit_id"] for u in rf["units"]], ["A1-u1", "A1-u2", "A1-u3"])
        self.assertEqual([u["text"] for u in risk_themes.shown(rf, 80)],          # no cross reference
                         ["Competition could harm our results.", "Rivals cut prices."])

    def test_identical_risk_text_shares_one_request(self):
        cfg = llm_cost.step_cfg("risk_themes")
        units = factor("A1", ["Rivals cut prices."]) + factor("A2", ["Rivals cut prices."]) \
            + factor("A3", ["Rivals cut prices sharply."])
        plans = risk_themes.plans(risk_themes.risk_factors(units), cfg)
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
        [plan] = risk_themes.plans(risk_themes.risk_factors(factor("A1", ["Rivals cut prices.", "Costs rise."])),
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
