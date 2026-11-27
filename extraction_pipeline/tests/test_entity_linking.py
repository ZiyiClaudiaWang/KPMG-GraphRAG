"""#91 P1: Claims linked to subsidiaries and segments by code (entities/annotation.py, step 6b)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.entities.annotation import annotate_mentions, match_key

MSFT, ACC, PRIOR_ACC = "0000789019", "0000789019-26-000001", "0000789019-26-000002"


def entry(entity_id, name, kind="subsidiary", source="exhibit_21", **changes):
    row = {"entity_id": entity_id, "entity_type": kind, "issuer_cik": MSFT, "name": name, "aliases": [],
           "accessions": [ACC, PRIOR_ACC], "prior_year_only_accessions": [], "review_status": "needs_review",
           "source": source}
    return row | changes


CATALOG = [
    entry("linkedin", "LinkedIn Corporation"),
    entry("rockstar", "Rockstar Games, Inc.", prior_year_only_accessions=[PRIOR_ACC]),
    entry("lord", "LORD Corporation"),
    entry("ic", "Intelligent Cloud", kind="segment", source="xbrl", aliases=["Intelligent Cloud Segment"]),
    entry("medsurg", "Medical-Surgical Solutions", kind="segment", source="xbrl"),
    entry("europe", "Europe", kind="segment", source="xbrl"),
    entry("converse-sub", "Converse Inc."),
    entry("converse-seg", "Converse", kind="segment", source="xbrl"),
    entry("msft-ireland", "Microsoft Ireland Research Unlimited Company"),
    entry("self-sub", "Microsoft Corporation"),         # a row equal to the registrant
    entry("registrant", "Microsoft", kind="registrant", source="config", aliases=["Microsoft Corporation"],
          review_status="reviewed"),
]


def model(text, wording, kind="unknown", ref="named"):
    return {"mention_text": wording, "entity_type": kind, "reference_kind": ref, "evidence_quote": text}


def annotate(text, mentions=(), accession=ACC, catalog=CATALOG, **kw):
    return annotate_mentions(text, list(mentions), issuer_cik=MSFT, accession=accession, catalog=catalog, **kw)


class MatchKey(unittest.TestCase):
    def test_legal_form_dashes_and_segment_word_are_ignored(self):
        self.assertEqual(match_key("Rockstar Games, Inc."), match_key("Rockstar Games"))
        self.assertEqual(match_key("Medical‑Surgical Solutions segment"), match_key("Medical-Surgical Solutions"))
        self.assertEqual(match_key("the Intelligent Cloud reportable segment"), "intelligent cloud")
        self.assertEqual(match_key("iPhone®"), "iphone")


class ModelMentions(unittest.TestCase):
    def test_unknown_mention_takes_the_catalog_type(self):
        text = "Rockstar Games released a new title."
        out = annotate(text, [model(text, "Rockstar Games")])
        m = out["entity_mentions"][0]
        self.assertEqual((m["match_status"], m["entity_type"], m["model_entity_type"]),
                         ("matched_auto", "subsidiary", "unknown"))
        self.assertEqual(out["related_subsidiary_ids"], ["rockstar"])

    def test_no_cue_word_needed_when_the_catalog_hits(self):
        text = "Intelligent Cloud revenue increased 20%."
        out = annotate(text, [model(text, "Intelligent Cloud", "segment")])
        self.assertEqual(out["entity_mentions"][0]["match_status"], "matched_auto")
        self.assertEqual(out["related_segment_ids"], ["ic"])

    def test_registrant_is_never_an_entity(self):
        text = "Microsoft Corporation reported record revenue."
        out = annotate(text, [model(text, "Microsoft Corporation", "subsidiary")])
        self.assertEqual(out["entity_mentions"][0]["match_status"], "registrant_reference")
        self.assertEqual(out["related_subsidiary_ids"], [])

    def test_prior_year_only_stays_a_candidate(self):
        text = "Rockstar Games released a new title."
        out = annotate(text, [model(text, "Rockstar Games")], accession=PRIOR_ACC)
        m = out["entity_mentions"][0]
        self.assertEqual(m["match_status"], "candidate_match_needs_review")
        self.assertEqual(m["candidate_entity_ids"], ["rockstar"])
        self.assertEqual(out["related_subsidiary_ids"], [])

    def test_guarded_place_name_needs_the_word_segment(self):
        place = "Restrictions in Europe may affect trade."
        out = annotate(place, [model(place, "Europe", "segment")])
        self.assertEqual(out["entity_mentions"][0]["match_status"], "needs_type_evidence_review")
        self.assertEqual(out["related_segment_ids"], [])
        seg = "Net sales in the Europe segment increased."
        self.assertEqual(annotate(seg, [model(seg, "Europe", "segment")])["related_segment_ids"], ["europe"])
        for seg in ("Europe net sales increased during 2025.", "Europe includes India and Africa."):
            self.assertEqual(annotate(seg)["related_segment_ids"], ["europe"], seg)
        listed = "Tariffs on imports from China, India, Europe and Vietnam rose."
        self.assertEqual(annotate(listed)["related_segment_ids"], [])

    def test_a_name_that_is_both_links_both(self):
        text = "Converse revenues decreased 10%."
        out = annotate(text, [model(text, "Converse", "segment")])
        m = out["entity_mentions"][0]
        self.assertEqual((m["entity_type"], m["also_entity_types"]), ("segment", ["subsidiary"]))
        self.assertEqual((out["related_segment_ids"], out["related_subsidiary_ids"]),
                         (["converse-seg"], ["converse-sub"]))

    def test_two_entries_with_one_key_are_ambiguous(self):
        cat = CATALOG + [entry("linkedin-2", "LinkedIn Ltd")]
        text = "LinkedIn revenue increased."
        out = annotate(text, [model(text, "LinkedIn")], catalog=cat)
        self.assertEqual(out["entity_mentions"][0]["match_status"], "ambiguous_catalog_match")
        self.assertEqual(out["related_subsidiary_ids"], [])

    def test_unmatched_mentions_keep_the_old_rules(self):
        text = "OpenAI is a strategic partner."
        out = annotate(text, [model(text, "OpenAI")])
        self.assertEqual(out["entity_mentions"][0]["match_status"], "needs_review")
        text = "Nuance revenue grew."
        out = annotate(text, [model(text, "Nuance", "subsidiary")])
        self.assertEqual(out["entity_mentions"][0]["match_status"], "needs_type_evidence_review")


class DictionaryPass(unittest.TestCase):
    def test_names_the_model_skipped_are_found(self):
        text = "LinkedIn revenue increased 9% driven by Talent Solutions."
        out = annotate(text, [])
        m = out["entity_mentions"][0]
        self.assertEqual((m["origin"], m["mention_text"], m["match_status"]), ("dictionary", "LinkedIn", "matched_auto"))
        self.assertEqual(text[m["char_start"]:m["char_end"]], "LinkedIn")
        self.assertEqual(out["related_subsidiary_ids"], ["linkedin"])

    def test_no_duplicate_when_the_model_tagged_it(self):
        text = "LinkedIn revenue increased."
        out = annotate(text, [model(text, "LinkedIn", "subsidiary")])
        self.assertEqual([(m["origin"], m["found_by"]) for m in out["entity_mentions"]],
                         [("model", ["dictionary", "model"])])
        self.assertEqual(out["related_subsidiary_ids"], ["linkedin"])

    def test_model_only_mentions_are_kept(self):
        # counterparties and generic references stay, for later use
        text = "OpenAI and our international subsidiaries were mentioned."
        out = annotate(text, [model(text, "OpenAI"), model(text, "international subsidiaries", "subsidiary", "generic")])
        self.assertEqual([(m["match_status"], m["found_by"]) for m in out["entity_mentions"]],
                         [("needs_review", ["model"]), ("generic_reference", ["model"])])

    def test_short_name_on_the_list_is_searched(self):
        cat = CATALOG + [entry("2k", "2K, Inc.")]
        text = "We publish through Rockstar Games, 2K, and Zynga."
        self.assertIn("2k", annotate(text, catalog=cat)["related_subsidiary_ids"])
        self.assertEqual(annotate("Sales of NBA 2K and WWE 2K grew.", catalog=cat)["entity_mentions"], [])

    def test_lower_case_words_are_not_names(self):
        cat = CATALOG + [entry("pc", "Public Cloud", kind="segment", source="xbrl")]
        self.assertEqual(annotate("We partner with all major public cloud providers.", catalog=cat)["entity_mentions"], [])
        text = "Public Cloud revenue grew."
        self.assertEqual(annotate(text, catalog=cat)["related_segment_ids"], ["pc"])
        lower = "Demand for public cloud services grew."
        out = annotate(lower, [model(lower, "public cloud", "segment")], catalog=cat)
        self.assertEqual(out["related_segment_ids"], [])

    def test_whole_words_only(self):
        self.assertEqual(annotate("Our landlord renewed the lease.")["entity_mentions"], [])

    def test_registrant_key_is_never_searched(self):
        self.assertEqual(annotate("Microsoft Corporation grew revenue.")["entity_mentions"], [])

    def test_longest_name_wins(self):
        text = "Microsoft Ireland Research Unlimited Company is our subsidiary."
        out = annotate(text)
        self.assertEqual([m["candidate_entity_ids"] for m in out["entity_mentions"]], [["msft-ireland"]])

    def test_dash_variants_in_text_match(self):
        text = "The Medical‑Surgical Solutions segment grew."
        self.assertEqual(annotate(text)["related_segment_ids"], ["medsurg"])

    def test_guarded_name_without_segment_word_is_not_linked(self):
        out = annotate("Demand in Europe weakened.")
        self.assertEqual(out["related_segment_ids"], [])
        self.assertEqual(out["entity_mentions"][0]["match_status"], "needs_type_evidence_review")

    def test_dictionary_can_be_turned_off(self):
        self.assertEqual(annotate("LinkedIn revenue increased.", dictionary=False)["entity_mentions"], [])


class CatalogStep(unittest.TestCase):
    """Step 6b: subsidiaries from step 6a, segments from the fact store, the registrant."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "output").mkdir()
        self.env = {k: os.environ.get(k) for k in ("PIPELINE_DATA_DIR", "PIPELINE_FACT_STORE", "MSFT_FACT_DB_PATH",
                                                   "PIPELINE_FILINGS")}
        os.environ["PIPELINE_DATA_DIR"] = str(self.tmp)
        os.environ["PIPELINE_FACT_STORE"] = ""     # empty, not unset: a store in the real .env must not apply
        os.environ["MSFT_FACT_DB_PATH"] = ""
        (self.tmp / "filings.yaml").write_text(
            "filings:\n"
            f"  - {{ticker: MSFT, cik: \"{MSFT}\", form: 10-K, reporting_period_end: \"2026-06-30\", accession: \"{ACC}\"}}\n")
        os.environ["PIPELINE_FILINGS"] = str(self.tmp / "filings.yaml")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp)

    def store(self, rows):
        import duckdb
        path = self.tmp / "facts.duckdb"
        conn = duckdb.connect(str(path))
        conn.execute("CREATE TABLE financial_facts_v1 (cik VARCHAR, accession_number VARCHAR, dimensions VARCHAR)")
        conn.executemany("INSERT INTO financial_facts_v1 VALUES (?, ?, ?)", rows)
        conn.close()
        return path

    def test_catalog_has_subsidiaries_segments_and_registrants(self):
        from src.entities import catalog as entity_catalog
        from src.common import filings
        f = next(x for x in filings() if x["ticker"] == "MSFT")
        cik, acc = str(f["cik"]).zfill(10), f["accession"]
        sub = entry("s1", "LinkedIn Corporation", issuer_cik=cik, accessions=[acc])
        (self.tmp / "output" / "subsidiary_catalog.json").write_text(json.dumps([sub]))
        dims = lambda m: json.dumps([{"axis": entity_catalog.SEGMENT_AXIS, "member": m}])
        store = self.store([(cik, acc, dims("msft:IntelligentCloudMember")),
                            (cik, acc, dims("msft:IntelligentCloudMember")),
                            (cik, "not-in-filings", dims("msft:OldSegmentMember")),
                            (cik, acc, json.dumps([{"axis": "srt:ProductOrServiceAxis", "member": "x:Y"}]))])
        catalog = entity_catalog.build(str(store))
        kinds = {(e["entity_type"], e["name"]) for e in catalog}
        self.assertIn(("subsidiary", "LinkedIn Corporation"), kinds)
        self.assertIn(("segment", "Intelligent Cloud"), kinds)
        self.assertNotIn(("segment", "Old Segment"), kinds)
        self.assertNotIn(("segment", "Y"), kinds)
        reg = next(e for e in catalog if e["entity_type"] == "registrant" and e["issuer_cik"] == cik)
        self.assertIn(acc, reg["accessions"])
        seg = next(e for e in catalog if e["entity_type"] == "segment")
        self.assertEqual((seg["source"], seg["accessions"]), ("xbrl", [acc]))
        text = "Intelligent Cloud revenue and LinkedIn revenue grew, Microsoft said."
        out = annotate_mentions(text, [], issuer_cik=cik, accession=acc, catalog=catalog)
        self.assertEqual((out["related_segment_ids"], out["related_subsidiary_ids"]), ([seg["entity_id"]], ["s1"]))

    def test_reviewed_segment_names_and_skipped_members(self):
        # config/entity_linking.yaml segment_names / segment_member_skip, on Walmart's members
        from src.entities import catalog as entity_catalog
        wmt, acc = "0000104169", "0000104169-26-000001"
        (self.tmp / "filings.yaml").write_text(
            "filings:\n"
            f"  - {{ticker: WMT, cik: \"{wmt}\", form: 10-K, reporting_period_end: \"2026-01-31\", accession: \"{acc}\"}}\n")
        dims = lambda m: json.dumps([{"axis": entity_catalog.SEGMENT_AXIS, "member": m}])
        store = self.store([(wmt, acc, dims(m)) for m in
                            ("wmt:SamsClubSegmentMember", "wmt:SamsClubMember", "wmt:CorporateAndSupportMember")])
        segs = [e for e in entity_catalog.build(str(store)) if e["entity_type"] == "segment"]
        self.assertEqual([(e["name"], e["review_status"], len(e["members"])) for e in segs],
                         [("Sam's Club", "reviewed", 2)])
        out = annotate_mentions("Sam's Club comparable sales grew.", [], issuer_cik=wmt, accession=acc,
                                catalog=segs)
        self.assertEqual(out["related_segment_ids"], [segs[0]["entity_id"]])

    def test_single_segment_companies_are_listed(self):
        from src.common import load_config
        self.assertEqual(set(load_config("entity_linking")["single_segment_companies"]), {"CASY", "TTWO"})

    def test_segment_label_from_qname(self):
        from src.entities.catalog import segment_label
        self.assertEqual(segment_label("aapl:GreaterChinaSegmentMember"), "Greater China Segment")
        self.assertEqual(segment_label("mu:CDBUMember"), "CDBU")
        self.assertEqual(match_key(segment_label("aapl:GreaterChinaSegmentMember")), "greater china")

    def test_without_inputs_the_catalog_is_registrants_only(self):
        from src.entities import catalog as entity_catalog
        self.assertEqual({e["entity_type"] for e in entity_catalog.build()}, {"registrant"})


if __name__ == "__main__":
    unittest.main()


class LoadMentions(unittest.TestCase):
    """Step 10 (#91 P3): one MENTIONS edge per Claim and linked entity."""

    def test_one_edge_per_claim_and_entity(self):
        from src.load import mention_rows
        text = "Converse revenues fell; Converse is a reset brand. LinkedIn grew."
        out = annotate(text)
        occ = {"claim_id": "c1", "entity_annotation_version": out["entity_annotation_version"], **out}
        rows = mention_rows([occ, dict(occ)])               # a repeated occurrence adds nothing
        self.assertEqual(sorted(r["entity_id"] for r in rows["subsidiary"]), ["converse-sub", "linkedin"])
        self.assertEqual([r["entity_id"] for r in rows["segment"]], ["converse-seg"])
        props = rows["subsidiary"][0]["props"]
        self.assertEqual((props["match_status"], props["found_by"]), ("matched_auto", ["dictionary"]))

    def test_candidates_are_not_loaded(self):
        from src.load import mention_rows
        text = "Rockstar Games released a new title."
        out = annotate(text, accession=PRIOR_ACC)            # prior-year-only: a candidate
        self.assertEqual(mention_rows([{"claim_id": "c1", **out}]), {"subsidiary": [], "segment": []})
