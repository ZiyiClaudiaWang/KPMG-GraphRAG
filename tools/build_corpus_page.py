"""Turn a corpus report into a dated static page we can host.

The dashboard is a live tool and stays local: it reads a graph and a fact store
with credentials, and it has no authentication in front of it. What a reader
outside the team needs is different - a frozen artifact with a date on it, that
cannot change between the deck being written and the meeting happening.

    python3 tools/corpus_report.py --json /tmp/corpus.json
    python3 tools/build_corpus_page.py --report /tmp/corpus.json \
        --output ~/linlinlin.online/kpmg/corpus-2026-11-16.html

Static HTML, no credentials, no server, no live query. It deploys by the same
push that deploys the rest of the site. The one tab it cannot carry is Ask,
because that runs retrieval; this is the Corpus and Checks views.

Same handling as the gold set page: `noindex,nofollow`, unlinked, and dated in
the filename so an older copy stays readable rather than being overwritten.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

CSS = """
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,sans-serif;margin:0;background:#fafafa;color:#1a1a1a}
header{background:#fff;border-bottom:1px solid #ddd;padding:16px 20px}
h1{font-size:17px;margin:0 0 6px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:#555;margin:28px 0 8px}
main{padding:0 20px 60px}
.prov{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#555;line-height:1.7}
.sub{font-size:12px;color:#666;max-width:92ch;margin:8px 0 0}
.warn{background:#ffe3e3;color:#9b1c1c;border-radius:6px;padding:10px 12px;margin:10px 0;font-size:13px}
table{border-collapse:collapse;width:100%;background:#fff;margin-top:6px}
td,th{border-bottom:1px solid #e4e4e4;padding:8px 11px;text-align:left;vertical-align:top}
th{background:#f2f2f2;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#555}
td.n{font-family:ui-monospace,Menlo,monospace;text-align:right;white-space:nowrap}
tr:hover{background:#fff8e5}
.pill{display:inline-block;border-radius:4px;padding:1px 7px;font-size:11px;white-space:nowrap}
.ok{background:#dcf5e7;color:#0a7a46}
.look{background:#fdf0d5;color:#8a5a00}
.counts{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:#333;line-height:1.9}
.note{font-size:11.5px;color:#777;max-width:70ch}
footer{border-top:1px solid #ddd;margin-top:40px;padding:14px 20px;font-size:11.5px;color:#888}
"""


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def shorten_paths(line: str) -> str:
    """Cut an absolute path down to its last two parts.

    The provenance line is the point of the page, but the terminal version
    prints the full path, which puts a home directory and a username on
    something we publish. Which store answered is what a reader needs; where it
    sat on one laptop is not.
    """
    return re.sub(
        r"(/[^\s]*/)([^/\s]+/[^/\s]+)",
        lambda m: "…/" + m.group(2),
        line,
    )


def table(headers: list[str], rows: list[list], numeric: set[int] | None = None) -> str:
    numeric = numeric or set()
    out = ["<table><thead><tr>"]
    out += [f"<th>{esc(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for i, cell in enumerate(row):
            css = " class=n" if i in numeric else ""
            out.append(f"<td{css}>{cell if str(cell).startswith('<') else esc(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def pill(text: str, ok: bool) -> str:
    return f"<span class='pill {"ok" if ok else "look"}'>{esc(text)}</span>"


def checks_from(report: dict) -> list[list]:
    """The same checks the dashboard shows, in the same order.

    Kept as a plain list rather than imported from the app, because the app
    imports streamlit and this has to run anywhere.
    """
    graph = report.get("graph") or {}
    store = report.get("store") or {}
    rows: list[list] = []

    def add(name: str, value, ok: bool, note: str) -> None:
        shown = f"{value:,}" if isinstance(value, int) else str(value)
        rows.append([name, shown, pill("pass" if ok else "look", ok), note])

    if graph.get("scope"):
        for name, count in graph["orphans"].items():
            add(f"graph · {name.replace('_', ' ')}", count, count == 0,
                "An unattached node is unreachable by every query the arm makes.")
        identity = graph["identity"]
        add("graph · companies with no ticker", identity["no_ticker"],
            identity["no_ticker"] == 0,
            "No ticker means the arm can answer about the wrong company.")
        add("graph · CIKs with more than one name",
            len(identity["ciks_with_multiple_names"]),
            not identity["ciks_with_multiple_names"],
            "Name variants for one company.")

    if store:
        dups = store["duplicates"]
        add("store · repeated fact_id", dups["repeated_fact_id"],
            dups["repeated_fact_id"] == 0, "The same identity twice is a loader bug.")
        add("store · statement duplicates", dups["statement_duplicates"], True,
            "One fact under more than one context in a filing. Usually benign.")
        add("store · comparative groups", dups["comparative_groups"], True,
            "Last year's figure inside this year's report. Expected.")
        add("store · comparatives that disagree on value", dups["restated_groups"],
            dups["restated_groups"] == 0,
            "A restatement, or two loaders disagreeing. Nobody has looked at these.")
        for name, count in store["dates"].items():
            add(f"store · {name.replace('_', ' ')}", count, count == 0,
                "A period that cannot be right for the form it sits on.")
        columns = store["column_population"]
        empty = [c for c, v in columns.items()
                 if c != "__numeric__" and v.get("all_null")]
        add("store · columns entirely NULL", ", ".join(empty) or "none", not empty,
            "An all-null column gets no type at all.")
        numeric = columns.get("__numeric__") or {}
        if numeric:
            rest = numeric["no_numeric_value"] - numeric["unparsed_numbers"]
            add("store · parseable numbers left empty", numeric["unparsed_numbers"],
                numeric["unparsed_numbers"] == 0,
                f"raw_value holds a number and numeric_value does not. The other "
                f"{rest:,} are text blocks and document metadata, which correctly "
                "have none.")
        missing = store["key_concepts"]["missing"]
        add("store · filings missing a key concept",
            sum(len(v) for v in missing.values()),
            not missing, "Revenue, net income, assets, cash, operating income.")
    return rows


def build(report: dict, generated_on: str) -> str:
    graph = report.get("graph") or {}
    store = report.get("store") or {}

    parts = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        "<meta name=viewport content=\"width=device-width,initial-scale=1\">",
        "<meta name=robots content=\"noindex,nofollow\">",
        f"<title>Corpus report {esc(generated_on)}</title>",
        f"<style>{CSS}</style>",
        "<header>",
        f"<h1>What is loaded in each arm — {esc(generated_on)}</h1>",
        "<div class=prov>" + "<br>".join(esc(shorten_paths(line)) for line in report["provenance"]) + "</div>",
        "<p class=sub>A snapshot, not a live view. Every number below was read at "
        "the time in the title and will not change. The live tool runs locally and "
        "also carries a ranking view, which needs credentials and so is not here.</p>",
        "</header><main>",
    ]

    for warning in graph.get("warnings", []):
        parts.append(f"<div class=warn>{esc(warning)}</div>")

    if graph.get("scope"):
        parts.append("<h2>Graph arm</h2>")
        nodes = " · ".join(f"{k} {v:,}" for k, v in graph["nodes_by_label"].items())
        edges = " · ".join(f"{k} {v:,}" for k, v in graph["relationships_by_type"].items())
        subs = graph["subsidiaries"]
        spread = graph["spread"]["claims_per_section"]
        parts.append(
            f"<div class=counts>nodes &nbsp;{esc(nodes)}<br>edges &nbsp;{esc(edges)}</div>")
        parts.append(
            f"<p class=note>Subsidiary coverage: {subs['parents_with_subsidiaries']} "
            f"parents with subsidiaries against {subs['ten_k_filings']} 10-K filings. "
            "Only a 10-K carries an Exhibit 21, so a 10-Q with none is not a gap.</p>")
        if spread.get("n"):
            parts.append(
                f"<p class=note>Claims per section: min {spread['min']} · median "
                f"{spread['median']} · max {spread['max']} over {spread['n']} sections. "
                "A wide spread means the extractor treated sections differently, which "
                "is where recall goes as the corpus grows.</p>")

    if store:
        totals = store["totals"]
        parts.append("<h2>Structured arm</h2>")
        parts.append(
            f"<div class=counts>{totals['filings']:,} filings · "
            f"{totals['companies']:,} companies · {totals['facts']:,} facts · "
            f"{totals['concepts']:,} concepts</div>")

        parts.append("<h2>Per filing</h2>")
        parts.append("<p class=note>Totals hide a filing that loaded thin or broke.</p>")
        parts.append(table(
            ["ticker", "form", "filing date", "facts", "concepts", "periods",
             "dimensioned", "dimensioned share"],
            [[r["ticker"], r["form"], r["filing_date"], f"{r['facts']:,}",
              f"{r['concepts']:,}", r["periods"], f"{r['dimensioned']:,}",
              r["dimensioned_share"]] for r in store["per_filing"]],
            numeric={3, 4, 5, 6, 7}))

        parts.append("<h2>Key concepts</h2>")
        parts.append(
            "<p class=note>Does every filing carry the figures a reader asks for "
            "first. One gap at two hundred filings is a bug that otherwise surfaces "
            "as a failed question weeks later.</p>")
        present = store["key_concepts"]["present"]
        by_accession = {r["accession"]: r["ticker"] for r in store["per_filing"]}
        concepts = list(present)
        accessions = list(next(iter(present.values()))) if present else []
        parts.append(table(
            ["filing"] + concepts,
            [[by_accession.get(a, a)] +
             [pill("yes", True) if present[c][a] else pill("no", False) for c in concepts]
             for a in accessions]))

    parts.append("<h2>Checks</h2>")
    rows = checks_from(report)
    looking = sum(1 for r in rows if "look" in r[2])
    parts.append(
        f"<p class=note>{looking} of {len(rows)} want a look. The oracle checks — "
        "accounting identity, segment sums, quarterly roll-up — are not built yet, "
        "so their absence is not a pass.</p>")
    parts.append(table(["check", "value", "", "what it catches"], rows, numeric={1}))

    parts.append("</main><footer>")
    parts.append(
        "Generated by tools/build_corpus_page.py from a tools/corpus_report.py run. "
        "Unlisted and noindex: it describes an internal corpus and another team's "
        "evaluation work.")
    parts.append("</footer></html>")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", required=True, help="JSON from corpus_report.py --json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dated", required=True,
                        help="the date to print, e.g. 2026-11-16. Passed in rather "
                             "than read from the clock so a rebuild of an old report "
                             "does not relabel it as today.")
    args = parser.parse_args()

    report = json.loads(Path(args.report).expanduser().read_text())
    page = build(report, args.dated)
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"{out}  ({len(page):,} bytes)")


if __name__ == "__main__":
    main()
