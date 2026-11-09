"""The subsidiaries step (Exhibit 21) and how step 8 loads it. No network: the fixture
exhibits are already saved, and the download test uses a fake SEC."""

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from src import entity_annotation, load, subsidiaries
from src.common import read_csv, read_jsonl

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
        self.real_get = subsidiaries.get
        subsidiaries.get = self.no_network

    def tearDown(self):
        subsidiaries.get = self.real_get
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
        return list(read_jsonl(subsidiaries.Paths.rows()))


class TestSubsidiariesStep(Ex21Case):

    def test_saved_exhibits_parse_without_network(self):
        subsidiaries.run()
        names = [(r["parent_ticker"], r["name"], r["jurisdiction_raw"]) for r in self.rows()]
        self.assertEqual(names, [
            ("TBL", "Table Example Asia Limited", "Hong Kong"),
            ("TBL", "Table Example Asia LLC", "Delaware, U.S."),
            ("TBL", "Table Example Operations International", "Ireland"),
            ("DSC", "Described’s Marketing Company", "Iowa"),
            ("DSC", "DES Stores, LLC", "Iowa"),
            ("DSC", "Example Wholesale, LLC", "Texas"),
        ])
        self.assertTrue(all(q["status"] == "OK" for q in read_csv(subsidiaries.Paths.qa())))

    def test_only_10ks_and_padded_ciks(self):
        subsidiaries.run()
        self.assertEqual({r["accession_number"] for r in self.rows()}, {TBL_10K, DSC_10K})
        self.assertEqual({r["parent_cik"] for r in self.rows()}, {"0000000001", "0000000002"})

    def test_rows_carry_country_and_provenance(self):
        subsidiaries.run()
        row = next(r for r in self.rows() if r["name"] == "Table Example Asia LLC")
        self.assertEqual((row["country"], row["state_or_region"]), ("United States", "Delaware"))
        self.assertEqual(row["fiscal_year"], "2025")
        self.assertTrue(row["source_url"].startswith("https://www.sec.gov/"))
        self.assertEqual(row["parser_version"], subsidiaries.PARSER_VERSION)

    def test_sub_id_is_parent_cik_and_normalised_name(self):
        a = subsidiaries.sub_id("0000000001", "Table Example Asia LLC")
        self.assertEqual(a, subsidiaries.sub_id("0000000001", "TABLE EXAMPLE ASIA LLC"))
        self.assertEqual(a, subsidiaries.sub_id("0000000001", "Table  Example Asia LLC."))
        self.assertNotEqual(a, subsidiaries.sub_id("0000000001", "Table Example Asia Limited"))
        self.assertNotEqual(a, subsidiaries.sub_id("0000000002", "Table Example Asia LLC"))
        uuid.UUID(a)                                   # a valid uuid
        subsidiaries.run()
        ids = [r["sub_id"] for r in self.rows()]
        subsidiaries.run()
        self.assertEqual(ids, [r["sub_id"] for r in self.rows()])

    def test_catalog_is_valid_and_covers_the_10q(self):
        subsidiaries.run()
        catalog = json.loads(subsidiaries.Paths.catalog().read_text(encoding="utf-8"))
        entity_annotation.validate_catalog(catalog)
        self.assertEqual(len(catalog), 6)
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
        subsidiaries.get = self.fake_sec
        subsidiaries.run()
        self.assertEqual(len([r for r in self.rows() if r["parent_ticker"] == "DSC"]), 3)
        self.assertEqual(len(self.calls), 3)            # index, exhibit, company name
        entry = {r["accession"]: r for r in read_csv(subsidiaries.Paths.manifest())}[DSC_10K]
        self.assertEqual(entry["source_url"], "https://www.sec.gov/Archives/edgar/data/2/000000000226000003/ex211.htm")
        subsidiaries.run()                              # saved now: no more requests
        self.assertEqual(len(self.calls), 3)

    def test_no_user_agent_is_a_check_not_a_stop(self):
        subsidiaries.run()                              # would raise if it tried the network
        qa = {q["accession"]: q for q in read_csv(subsidiaries.Paths.qa())}
        self.assertIn("SEC_USER_AGENT", qa[DSC_10K]["status"])
        self.assertEqual(qa[TBL_10K]["status"], "OK")

    def test_network_failure_is_a_check_not_a_stop(self):
        os.environ["SEC_USER_AGENT"] = "Test test@example.com"

        def offline(url):
            raise OSError("network unreachable")
        subsidiaries.get = offline
        subsidiaries.run()
        qa = {q["accession"]: q for q in read_csv(subsidiaries.Paths.qa())}
        self.assertIn("download failed", qa[DSC_10K]["status"])
        self.assertEqual({r["parent_ticker"] for r in self.rows()}, {"TBL"})


class TestLoad(Ex21Case):

    def test_subsidiaries_load_without_claims(self):
        subsidiaries.run()
        rows = load.build_rows()
        self.assertEqual(len(rows["subsidiaries"]), 6)
        self.assertEqual(rows["claims"], [])
        self.assertEqual({c["cik"] for c in rows["companies"]}, {"0000000001", "0000000002"})
        self.assertEqual({c["name"] for c in rows["companies"]},
                         {"Table Example Inc.", "Described Example Stores Inc."})

    def test_preview_has_owns(self):
        subsidiaries.run()
        load.run(dry_run=True)
        preview = (self.tmp / "output" / "load_preview.cypher").read_text(encoding="utf-8")
        self.assertIn("// subsidiaries: 6 rows", preview)
        self.assertIn("MERGE (c)-[o:OWNS {accession_number: r.accession_number, row_order: r.row_order", preview)
        self.assertIn("MATCH (c:Company {cik: r.parent_cik", preview)

    def test_nothing_to_load_says_so(self):
        with self.assertRaises(SystemExit):
            load.build_rows()


if __name__ == "__main__":
    unittest.main()
