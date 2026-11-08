"""Tests for how fetch_edgar.py chooses filings, on a fake EDGAR (no network).

Run:  python -m pytest -q tests   (or: python tests/test_fetch.py)
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_edgar  # noqa: E402

# A made-up company: four 10-Ks on the first page, one 10-K/A, a 10-Q, and one older
# 10-K that is only on the extra page.
RECENT = {
    "form":            ["10-K",       "10-Q",       "10-K/A",     "10-K",       "10-K",       "10-K"],
    "accessionNumber": ["A-26-000001", "A-26-000002", "A-25-000009", "A-25-000003", "A-24-000004", "A-23-000005"],
    "reportDate":      ["2026-09-30", "2026-06-30", "2025-09-30", "2025-09-30", "2024-09-30", "2023-09-30"],
}
OLDER = {"form": ["10-K"], "accessionNumber": ["A-20-000006"], "reportDate": ["2020-09-30"]}
SUBMISSIONS = {"name": "Example Corp", "filings": {"recent": RECENT,
                                                   "files": [{"name": "CIK0000000001-submissions-001.json"}]}}
CALLS = []


def fake_get(url):
    CALLS.append(url)
    if url.endswith("CIK0000000001.json"):
        return json.dumps(SUBMISSIONS).encode()
    if url.endswith("submissions-001.json"):
        return json.dumps(OLDER).encode()
    raise AssertionError(f"unexpected url {url}")


fetch_edgar.get = fake_get
TMAP = lambda: {"EXM": ("0000000001", "EXAMPLE CORP")}  # noqa: E731


def args(tickers=(), years=3, fy=None, from_filings=None):
    return SimpleNamespace(tickers=list(tickers), years=years, fy=fy, from_filings=from_filings)


def accessions(targets):
    return [t["accession"] for t in targets]


def test_most_recent_years_skips_amendments_and_10q():
    targets, log = fetch_edgar.parse_targets(args(["EXM"], years=2), TMAP)
    assert accessions(targets) == ["A-26-000001", "A-25-000003"]
    assert log == []


def test_fy_option_picks_that_year():
    targets, _ = fetch_edgar.parse_targets(args(["EXM"], fy=["2025"]), TMAP)
    assert accessions(targets) == ["A-25-000003"]
    assert targets[0]["fiscal_year"] == "2025" and targets[0]["name"] == "Example Corp"


def test_ticker_colon_years():
    targets, _ = fetch_edgar.parse_targets(args(["exm:2024,2026"]), TMAP)
    assert sorted(accessions(targets)) == ["A-24-000004", "A-26-000001"]


def test_old_year_reads_extra_page_only_when_needed():
    CALLS.clear()
    fetch_edgar.parse_targets(args(["EXM:2025"]), TMAP)
    assert not any("submissions-001" in c for c in CALLS)
    CALLS.clear()
    targets, _ = fetch_edgar.parse_targets(args(["EXM:2020"]), TMAP)
    assert accessions(targets) == ["A-20-000006"]
    assert any("submissions-001" in c for c in CALLS)


def test_missing_year_and_ticker_are_logged():
    targets, log = fetch_edgar.parse_targets(args(["EXM:2019", "NOPE"]), TMAP)
    assert targets == []
    assert [r["status"] for r in log] == ["no 10-K with fiscal year 2019 (period end in 2019)",
                                          "ticker not found"]


def test_from_filings_takes_only_10k_rows(tmp_path=None):
    import tempfile
    folder = Path(tmp_path or tempfile.mkdtemp())
    yaml_file = folder / "filings.yaml"
    yaml_file.write_text(
        "filings:\n"
        '  - {ticker: AAPL, cik: "0000320193", form: 10-K, period_end: "2025-09-27", accession: "0000320193-25-000079"}\n'
        '  - {ticker: AAPL, cik: "0000320193", form: 10-Q, period_end: "2026-03-28", accession: "0000320193-26-000013"}\n'
        '  - {ticker: MDT,  cik: "1613103",    form: 10-K, period_end: "2026-04-24", accession: "0001628280-26-044354"}\n',
        encoding="utf-8")
    targets, log = fetch_edgar.parse_targets(args(from_filings=str(yaml_file)), TMAP)
    assert accessions(targets) == ["0000320193-25-000079", "0001628280-26-044354"]
    assert [t["fiscal_year"] for t in targets] == ["2025", "2026"]
    assert targets[1]["cik"] == "0001613103"          # padded to ten digits
    assert log == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
