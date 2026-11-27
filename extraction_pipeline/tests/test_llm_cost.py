"""Settings, prices and request fields of the LLM steps (2b, 5b; 7 and 8b request fields)."""

import unittest

from src.llm import cost as llm_cost


class LlmCostTest(unittest.TestCase):
    def test_steps_have_their_own_model_and_budget(self):
        for step in ("page_breaks",):
            cfg = llm_cost.step_cfg(step)
            self.assertIn("budget_usd", cfg)
            self.assertIsNotNone(cfg["price_input_per_1m"], f"{cfg['model']} missing from prices:")

    def test_regional_uplift_only_for_new_models_on_regional_endpoints(self):
        us, world = "https://us.api.openai.com/v1", "https://api.openai.com/v1"
        self.assertAlmostEqual(llm_cost.prices_for("gpt-5.4", us)["price_output_per_1m"], 16.5)
        self.assertAlmostEqual(llm_cost.prices_for("gpt-5.4", world)["price_output_per_1m"], 15.0)
        self.assertAlmostEqual(llm_cost.prices_for("gpt-4.1-mini", us)["price_output_per_1m"], 1.6)
        self.assertIsNone(llm_cost.prices_for("not-a-model", us)["price_input_per_1m"])

    def test_request_fields_by_model_family(self):
        old = llm_cost.model_params({}, {"model": "gpt-4.1-mini"}, 100)
        self.assertEqual(old, {"max_completion_tokens": 100, "temperature": 0})
        new = llm_cost.model_params({}, {"model": "gpt-5.4", "reasoning_effort": "low",
                                         "max_reasoning_tokens": 2000}, 100)
        self.assertEqual(new, {"max_completion_tokens": 2100, "reasoning_effort": "low"})

    def test_label_request_fields_by_model_family(self):
        """Steps 7 / 8b: reasoning keys reach reasoning models only, so a gpt-4.1-mini request
        (and its request hash) is the same with or without them in settings."""
        from src.label.requests import model_fields
        base = {"temperature": 0, "reasoning_effort": "low", "max_reasoning_tokens": 2000}
        old = model_fields({"max_completion_tokens": 2000}, dict(base, model="gpt-4.1-mini"))
        self.assertEqual(old, {"max_completion_tokens": 2000, "temperature": 0})
        new = model_fields({"max_completion_tokens": 2000}, dict(base, model="gpt-5.4-mini"))
        self.assertEqual(new, {"max_completion_tokens": 4000, "reasoning_effort": "low"})
        none = model_fields({"max_completion_tokens": 2000}, {"model": "gpt-5-mini", "temperature": 0})
        self.assertEqual(none, {"max_completion_tokens": 2000})


if __name__ == "__main__":
    unittest.main()
