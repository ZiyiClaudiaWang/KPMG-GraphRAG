"""Step 1b (subsidiaries) and how step 8 loads it. No network: the fixture exhibits are
already saved, and the download tests use a fake SEC."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src import entity_annotation, load, subsidiaries
from src.common import ROOT, Paths, read_csv, read_jsonl
from src.ex21 import edgar

FIXTURES = Path(__file__).parent / "fixtures" / "ex21"
TBL_10K, TBL_10Q, DSC_10K = "0000000001-25-000001", "0000000001-26-000002", "0000000002-26-000003"


class Ex21Case(unittest.TestCase):
    """Each test gets a fresh data folder holding the saved fixture exhibits."""

    def setUp(self):
        self.saved_env = {k: os.environ.get(k) for k in
                          ("PIPELINE_DATA_DIR", "PIPELINE_FILINGS", "SEC_USER_AGENT")}
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURES / "raw", self.tmp / "ex21" / "raw")
        shutil.copy(FIXTURES / "manifest.csv", self.tmp / "ex21" / "manifest.csv")
        os.environ["PIPELINE_DATA_DIR"] = str(self.tmp)
        os.environ["PIPELINE_FILINGS"] = str(FIXTURES / "filings.yaml")
        os.environ.pop("SEC_USER_AGENT", None)
        self.real_get, self.real_env = edgar.get, subsidiaries.load_env
        edgar.get = self.no_network
        subsidiaries.load_env = lambda: None       # do not pick up a developer's .env

    def tearDown(self):
        edgar.get, subsidiaries.load_env = self.real_get, self.real_env
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def no_network(url):
        raise AssertionError(f"tried to download {url}")

    def rows(self):
        return list(read_jsonl(Paths.subsidiaries()))

    def qa(self):
        return {q["accession"]: q for q in read_csv(Paths.subsidiary_qa())}


class TestSubsidiariesStep(Ex21Case):

    def test_saved_exhibits_parse_without_network(self):
        subsidiaries.run()
        got = [(r["parent_ticker"], r["name"], r["country"], r["state_or_region"]) for r in self.rows()]
        self.assertEqual(got, [
            ("TBL", "Table Example Asia Limited", "Hong Kong", ""),
            ("TBL", "Table Example Asia LLC", "United States", "Delaware"),
            ("TBL", "Table Example Operations International", "Ireland", ""),
            ("TBL", "Table Example Arabia LLC", "Saudi Arabia", ""),
            ("TBL", "Table Example Regional Headquarter Company", "Saudi Arabia", ""),
            ("TBL", "Table Example Canada Ltd.", "Canada", "Ontario"),
            ("TBL", "Table Example Cayman Ltd.", "Cayman Islands", ""),
            ("DSC", "Described’s Marketing Company", "United States", "Iowa"),
            ("DSC", "DES Stores, LLC", "United States", "Iowa"),
            ("DSC", "Example Wholesale, LLC", "United States", "Texas"),
        ])
        self.assertTrue(all(q["status"] == "OK" for q in self.qa().values()))

    def test_registrant_row_is_dropped_and_counted(self):
        subsidiaries.run()
        self.assertNotIn("Table Example, Inc.", [r["name"] for r in self.rows()])
        self.assertEqual(self.qa()[TBL_10K]["registrant_rows_dropped"], "1")
        self.assertEqual(self.qa()[DSC_10K]["registrant_rows_dropped"], "0")

    def test_only_10ks_padded_ciks_and_reporting_period_end(self):
        subsidiaries.run()
        self.assertEqual({r["accession_number"] for r in self.rows()}, {TBL_10K, DSC_10K})
        self.assertEqual({r["parent_cik"] for r in self.rows()}, {"0000000001", "0000000002"})
        row = self.rows()[0]
        self.assertEqual(row["reporting_period_end"], "2025-09-30")
        for gone in ("fiscal_year", "period_end", "parent_name"):
            self.assertNotIn(gone, row)

    def test_rows_carry_identity_keys_and_provenance(self):
        subsidiaries.run()
        row = next(r for r in self.rows() if r["name"] == "Table Example Asia LLC")
        self.assertEqual(row["normalised_name"], "table example asia llc")
        self.assertEqual(row["entity_key"], "table example asia")
        self.assertEqual(row["immediate_parent"], "TBL")
        self.assertTrue(row["source_url"].startswith("https://www.sec.gov/"))
        self.assertEqual(row["parser_version"], subsidiaries.PARSER_VERSION)

    def test_sub_ids_are_stable(self):
        subsidiaries.run()
        ids = [r["sub_id"] for r in self.rows()]
        subsidiaries.run()
        self.assertEqual(ids, [r["sub_id"] for r in self.rows()])
        self.assertEqual(len(set(ids)), len(ids))

    def test_catalog_is_valid_and_covers_the_10q(self):
        subsidiaries.run()
        catalog = json.loads(Paths.subsidiary_catalog().read_text(encoding="utf-8"))
        entity_annotation.validate_catalog(catalog)
        self.assertEqual(len(catalog), 10)
        tbl = next(e for e in catalog if e["name"] == "Table Example Asia LLC")
        self.assertEqual(tbl["accessions"], [TBL_10K, TBL_10Q])
        self.assertEqual(tbl["review_status"], "needs_review")
        self.assertFalse((self.tmp / "entity_catalog.json").exists())   # the shared catalog is untouched

    def test_10k_dropped_from_filings_yaml_drops_out(self):
        subsidiaries.run()
        short = self.tmp / "filings.yaml"
        short.write_text((FIXTURES / "filings.yaml").read_text(encoding="utf-8").split("  - {ticker: DSC")[0],
                         encoding="utf-8")
        os.environ["PIPELINE_FILINGS"] = str(short)
        subsidiaries.run()
        self.assertEqual({r["parent_ticker"] for r in self.rows()}, {"TBL"})

    def test_unmapped_jurisdiction_is_flagged(self):
        raw = self.tmp / "ex21" / "raw" / f"DSC_FY2026_{DSC_10K}_ex21.htm"
        raw.write_text("<table><tr><td>Example Atlantis Ltd.</td><td>Atlantis</td></tr>"
                       "<tr><td>Example Iowa Co.</td><td>Iowa</td></tr></table>", encoding="utf-8")
        subsidiaries.run()
        row = next(r for r in self.rows() if r["name"] == "Example Atlantis Ltd.")
        self.assertEqual((row["jurisdiction_raw"], row["country"]), ("Atlantis", ""))
        qa = self.qa()[DSC_10K]
        self.assertEqual(qa["unmapped_jurisdiction"], "1")
        self.assertIn("not mapped to a country: Atlantis", qa["status"])

    def test_repeated_listing_is_one_row(self):
        raw = self.tmp / "ex21" / "raw" / f"DSC_FY2026_{DSC_10K}_ex21.htm"
        raw.write_text("<table><tr><td>Example – Cayman Partners, Ltd</td><td>Cayman Islands</td></tr>"
                       "<tr><td>Example Iowa Co.</td><td>Iowa</td></tr>"
                       "<tr><td>Example Cayman Partners, Ltd.</td><td>Cayman Islands</td></tr></table>",
                       encoding="utf-8")
        subsidiaries.run()
        rows = [r for r in self.rows() if r["parent_ticker"] == "DSC"]
        self.assertEqual([r["name"] for r in rows], ["Example – Cayman Partners, Ltd", "Example Iowa Co."])
        self.assertIn("also_listed_as_row_3", rows[0]["flags"])
        qa = self.qa()[DSC_10K]
        self.assertEqual((qa["rows_parsed"], qa["duplicate_names"]), ("3", "1"))

class TestDownload(Ex21Case):

    INDEX = ('<table><tr><td>1</td><td>10-K</td><td><a href="/ix?doc=/Archives/x/main.htm">main.htm</a></td>'
             '<td>10-K</td></tr><tr><td>5</td><td>Subsidiaries</td>'
             '<td><a href="/Archives/edgar/data/2/000000000226000003/ex211.htm">ex211.htm</a></td>'
             '<td>EX-21.1</td></tr></table>')

    def setUp(self):
        super().setUp()
        (self.tmp / "ex21" / "raw" / f"DSC_FY2026_{DSC_10K}_ex21.htm").unlink()
        self.calls = []

    def fake_sec(self, url):
        self.calls.append(url)
        if url.endswith("-index.html"):
            return self.INDEX.encode()
        if url.endswith(".json"):
            return json.dumps({"name": "DESCRIBED EXAMPLE STORES INC"}).encode()
        if url.endswith("ex211.htm"):
            return (FIXTURES / "raw" / f"DSC_FY2026_{DSC_10K}_ex21.htm").read_bytes()
        raise AssertionError(url)

    def test_downloads_missing_exhibit_once(self):
        os.environ["SEC_USER_AGENT"] = "Test test@example.com"
        edgar.get = self.fake_sec
        subsidiaries.run()
        self.assertEqual(len([r for r in self.rows() if r["parent_ticker"] == "DSC"]), 3)
        self.assertEqual(len(self.calls), 3)            # index, exhibit, registrant name
        entry = {r["accession"]: r for r in read_csv(Paths.ex21_manifest())}[DSC_10K]
        self.assertEqual(entry["source_url"], "https://www.sec.gov/Archives/edgar/data/2/000000000226000003/ex211.htm")
        self.assertEqual(entry["registrant_name"], "DESCRIBED EXAMPLE STORES INC")
        self.assertEqual(entry["reporting_period_end"], "2026-04-30")
        subsidiaries.run()                              # saved now: no more requests
        self.assertEqual(len(self.calls), 3)

    def test_no_user_agent_is_a_check_not_a_stop(self):
        subsidiaries.run()                              # would raise if it tried the network
        self.assertIn("SEC_USER_AGENT", self.qa()[DSC_10K]["status"])
        self.assertEqual(self.qa()[TBL_10K]["status"], "OK")

    def test_network_failure_is_a_check_not_a_stop(self):
        os.environ["SEC_USER_AGENT"] = "Test test@example.com"

        def offline(url):
            raise OSError("network unreachable")
        edgar.get = offline
        subsidiaries.run()
        self.assertIn("download failed", self.qa()[DSC_10K]["status"])
        self.assertEqual({r["parent_ticker"] for r in self.rows()}, {"TBL"})

    def test_old_manifest_column_still_read(self):
        """A manifest written before this change names the EDGAR name parent_name."""
        path = self.tmp / "ex21" / "manifest.csv"
        path.write_text(path.read_text(encoding="utf-8").replace("registrant_name", "parent_name"),
                        encoding="utf-8")
        (self.tmp / "ex21" / "raw").joinpath(f"DSC_FY2026_{DSC_10K}_ex21.htm").write_bytes(
            (FIXTURES / "raw" / f"DSC_FY2026_{DSC_10K}_ex21.htm").read_bytes())
        subsidiaries.run()
        self.assertEqual(self.qa()[TBL_10K]["registrant_rows_dropped"], "1")


class TestLoad(Ex21Case):

    def test_subsidiaries_load_without_claims(self):
        subsidiaries.run()
        rows = load.build_rows()
        self.assertEqual(len(rows["subsidiaries"]), 10)
        self.assertEqual(rows["claims"], [])
        self.assertEqual(rows["companies"], [])        # parents are made by the OWNS query itself

    def test_preview_has_owns_and_never_sets_company_name(self):
        subsidiaries.run()
        load.run(dry_run=True)
        preview = (self.tmp / "output" / "load_preview.cypher").read_text(encoding="utf-8")
        self.assertIn("// subsidiaries: 10 rows", preview)
        query = load.QUERIES["subsidiaries"]
        self.assertIn("MERGE (c:Company {cik: r.parent_cik", query)
        self.assertIn("o.reporting_period_end = r.reporting_period_end", query)
        self.assertIn("s.entity_key = r.entity_key", query)
        self.assertNotIn("c.name", query)
        self.assertNotIn("fiscal_year", query)

    def test_nothing_to_load_says_so(self):
        with self.assertRaises(SystemExit):
            load.build_rows()


if __name__ == "__main__":
    unittest.main()
