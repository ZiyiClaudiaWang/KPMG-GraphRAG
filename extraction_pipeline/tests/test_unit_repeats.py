"""Step-4 repeat levels: text, text + context, text + context + section_path."""

from src.report_unit_repeats import build


def unit(uid, acc, text, path="Item 7 > Results", role="body", prefilter=None, ticker="AAA", start=0):
    return {"unit_id": uid, "accession_number": acc, "ticker": ticker, "section_key": "item-7",
            "section_type": "mda", "section_path": path, "role": role, "prefilter": prefilter,
            "text": text, "char_start": start}


def section(acc, sentences, path="Item 7 > Results", ticker="AAA"):
    units = [unit(f"{acc}-t", acc, "Item 7", role="section_title", prefilter="section_title", ticker=ticker)]
    for k, text in enumerate(sentences):
        units.append(unit(f"{acc}-{k}", acc, text, path=path, ticker=ticker, start=k + 1))
    return units


def by_id(rows):
    return {r["unit_id"]: r for r in rows}


def test_three_levels():
    units = (section("A1", ["Intro one.", "Revenue rose  5%."])                    # first filing
             + section("A2", ["Intro one.", "Revenue rose 5%."])                   # same context
             + section("A3", ["Other intro.", "Revenue rose 5%."])                 # other context
             + section("A4", ["Intro one.", "Revenue rose 5%."], path="Item 7 > Other")  # other path
             + section("B1", ["Intro one.", "Revenue rose 5%."], ticker="BBB"))    # other company
    rows, table = build(units, 4, {"A1": 0, "A2": 1, "A3": 2, "A4": 3, "B1": 4})
    r = by_id(rows)
    assert r["A3-1"]["text_group"] == r["A1-1"]["text_group"] and r["A3-1"]["context_group"] is None
    assert r["A4-1"]["context_group"] == r["A1-1"]["context_group"] and r["A4-1"]["path_group"] is None
    assert r["A2-1"]["path_group"] == r["A1-1"]["path_group"]
    assert r["A2-1"]["path_first_unit_id"] == "A1-1"
    assert r["B1-1"]["text_group"] is None                       # repeats never cross companies
    assert all("A1-t" != x["unit_id"] for x in rows)            # titles are not counted
    text = {t["ticker"]: t for t in table if t["level"] == "text"}
    # "Intro one." in A1, A2, A4 and "Revenue rose 5%." in all four; "Other intro." once
    assert text["AAA"]["repeated_units"] == 7 and text["AAA"]["reusable_units"] == 5


def test_repeats_inside_one_filing_only_are_left_out():
    units = section("A1", ["Same sentence.", "Same sentence."])
    rows, _ = build(units, 4, {"A1": 0})
    assert all(r["text_group"] is None for r in rows)
