"""The mock and the real structured arm must describe a fact the same way.

`metadata.dimensions` was a dict keyed by axis in the mock and a list of
records in the arm. Both are truthy JSON containers, so a consumer written
against one carried on against the other with the wrong values and nothing
raised. Eval's `evidence_key()` feeds this straight into `EvalKey(dims=...)`,
so a comparison could be wrong without a single failure.

One checker, both producers, so they cannot drift apart again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mock"))
sys.path.insert(0, str(ROOT / "structured"))

DIMENSION_KEYS = {"axis", "member", "type"}


def check_dimensions(value, who):
    """A list of records, each naming an axis, a member and which kind it is."""
    assert isinstance(value, list), f"{who}: dimensions is {type(value).__name__}, not a list"
    for entry in value:
        assert isinstance(entry, dict), f"{who}: a dimension is {type(entry).__name__}"
        assert DIMENSION_KEYS <= set(entry), f"{who}: {sorted(entry)} is missing {sorted(DIMENSION_KEYS - set(entry))}"
        assert entry["type"] in ("explicit", "typed"), f"{who}: type={entry['type']!r}"
        for key in ("axis", "member"):
            assert isinstance(entry[key], str) and entry[key], f"{who}: {key}={entry[key]!r}"
            # A member a filer never tagged cannot be matched against one it
            # did. The mock used to return "Intelligent Cloud" where the arm
            # returns "msft:IntelligentCloudMember".
            assert ":" in entry[key], f"{who}: {key}={entry[key]!r} is not a qualified name"


def test_the_mock_returns_the_shape_the_arm_returns():
    from mock_retrieval_tools import structured_search

    seen_populated = False
    for item in structured_search("Intelligent Cloud revenue"):
        check_dimensions(item["metadata"]["dimensions"], "mock")
        seen_populated = seen_populated or bool(item["metadata"]["dimensions"])
    assert seen_populated, "no mock fact carried a dimension, so nothing was checked"


@pytest.mark.skipif(not os.environ.get("MSFT_FACT_DB_PATH"),
                    reason="needs a fact store: set MSFT_FACT_DB_PATH")
def test_the_real_arm_returns_it_too():
    import structured_query_v1

    seen_populated = False
    for item in structured_query_v1.structured_search("Microsoft revenue", {"ticker": "MSFT"}):
        check_dimensions(item["metadata"]["dimensions"], "structured arm")
        seen_populated = seen_populated or bool(item["metadata"]["dimensions"])
    assert seen_populated, "no fact carried a dimension, so nothing was checked"
