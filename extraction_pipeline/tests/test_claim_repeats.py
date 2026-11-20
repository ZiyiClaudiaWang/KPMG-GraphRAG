from src.report_claim_repeats import build_report


def claim(claim_id, accession, quote, cik="0000000001"):
    return {
        "claim_id": claim_id,
        "accession_number": accession,
        "quote": quote,
        "section_id": "item-1a",
        "provenance": {"cik": cik},
    }


def test_groups_exact_text_across_filings_of_the_same_company():
    report = build_report([
        claim("a1", "annual", "Revenue rose  5%."),
        claim("a2", "annual", "Revenue rose 5%."),
        claim("q1", "quarterly", "Revenue rose\n5%.", cik=1),
        claim("q2", "quarterly", "Revenue rose 6%."),
        claim("q3", "quarterly", "Revenue rose 5%", cik=1),
        claim("other", "other", "Revenue rose 5%.", cik=2),
        claim("unknown", "unknown", "Revenue rose 5%.", cik=None),
    ])

    assert report["total_claims"] == 7
    assert report["skipped_claims"] == 1
    assert report["repeat_groups"] == 1
    assert report["claims_in_repeat_groups"] == 3
    group, = report["groups"]
    assert group["filing_count"] == 2
    assert [item["claim_id"] for item in group["occurrences"]] == ["a1", "a2", "q1"]


def test_repeated_text_within_only_one_filing_is_excluded():
    report = build_report([
        claim("a1", "annual", "The supplier is overseas."),
        claim("a2", "annual", "The supplier is overseas."),
    ])
    assert report["groups"] == []
