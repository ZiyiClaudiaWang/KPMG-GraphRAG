"""checks.entity_inputs: the checks Kelly asked for on the 233-filing subsidiaries and segments."""

import unittest

from src.checks import entity_inputs as rep


def row(sub_id, name, key, acc, period, ticker="YRS"):
    return {"sub_id": sub_id, "name": name, "entity_key": key, "parent_ticker": ticker,
            "accession_number": acc, "reporting_period_end": period}


class TestReportEntityInputs(unittest.TestCase):
    def test_same_subsidiary_twice_in_one_10k_is_reported(self):
        rows = [row("a", "A Ltd", "a", "k1", "2024-06-30"), row("a", "A Ltd", "a", "k1", "2024-06-30")]
        kinds = [r["kind"] for r in rep.repeats(rows)]
        self.assertEqual(sum(k.startswith("same sub_id") for k in kinds), 1)

    def test_two_legal_entities_with_one_core_name_are_reported(self):
        rows = [row("a", "Asia LLC", "asia", "k1", "2024-06-30"), row("b", "Asia Limited", "asia", "k1", "2024-06-30")]
        self.assertEqual([r["count"] for r in rep.repeats(rows)], [2])

    def test_nothing_reported_for_one_subsidiary_in_two_years(self):
        rows = [row("a", "A Ltd", "a", "k1", "2023-06-30"), row("a", "A Ltd", "a", "k2", "2024-06-30")]
        self.assertEqual(rep.repeats(rows), [])

    def test_big_change_between_years_is_flagged(self):
        rows = [row(f"s{i}", "x", f"x{i}", "k1", "2023-06-30") for i in range(10)]
        rows += [row(f"s{i}", "x", f"x{i}", "k2", "2024-06-30") for i in range(3)]
        years = rep.subsidiaries_by_year(rows)
        self.assertEqual([y["flag"] for y in years], ["", "BIG CHANGE"])
        self.assertEqual(years[1]["change"], -0.7)


if __name__ == "__main__":
    unittest.main()
