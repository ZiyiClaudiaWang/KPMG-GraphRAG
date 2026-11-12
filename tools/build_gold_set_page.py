"""Build the hosted Gold Set page from the gold set and our hops file.

The page at linlinlin.online/kpmg/gold-set-v2 was hand-built once and went
stale within a week. This regenerates it from the two files that actually
change, so the page cannot disagree with them:

    python3 tools/build_gold_set_page.py \
        --gold "~/Projects/kpmg-eval/Week 8/data/benchmark_v2_2.jsonl" \
        --hops ~/Desktop/hops_v2_graphrag.csv \
        --output ~/linlinlin.online/kpmg/gold-set-v2.html

What it adds over the first version: the four columns we derive in
`fill_hops.py` — which arm serves a question, how many edges it walks on our
ontology, and why not when the answer is no. That is the part a reader cannot
get from the gold set alone, and it is what makes the page worth hosting
rather than sending a spreadsheet.

The page stays `noindex,nofollow` and unlinked. It carries another team's
evaluation data, so it is unlisted on purpose.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

# Measured, not derived: what came back when the arms were actually asked.
STATE_LABEL = {
    "green": ("answered", "ok"),
    "yellow": ("found, not first", "part"),
    "red": ("not found", "blocked"),
    "grey": ("not a retrieval test", "na"),
}

# Support reads as a word, with the colour doing the work a legend would.
SUPPORT_LABEL = {
    "yes": ("graph answers it", "ok"),
    "partial": ("graph answers half", "part"),
    "no": ("structured arm", "other"),
    "blocked": ("blocked", "blocked"),
    "n/a": ("no path expected", "na"),
}

CSS = """
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,sans-serif;margin:0;background:#fafafa;color:#1a1a1a}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:14px 20px;z-index:10}
h1{font-size:17px;margin:0 0 4px}
.sub{font-size:12px;color:#666;margin-bottom:10px;max-width:92ch}
.sub a{color:#0a3d91}
input,select{font:14px/1 inherit;padding:6px 9px;border:1px solid #ccc;border-radius:6px;margin-right:6px}
#count{color:#666;font-size:13px;margin-left:4px}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #e4e4e4;padding:10px 12px;vertical-align:top;text-align:left}
th{background:#f2f2f2;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#555;position:sticky;top:118px}
tr:hover{background:#fff8e5}
.qid{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#777;white-space:nowrap}
.q{max-width:40ch}
.a{max-width:32ch;color:#0a7a46}
.a.ref{color:#b4480b}
.tag{display:inline-block;background:#eceff4;border-radius:4px;padding:1px 6px;font-size:11px;margin:1px 2px 1px 0;white-space:nowrap}
.tag.route{background:#ddeaff;color:#0a3d91}
.tag.trap{background:#ffe3e3;color:#9b1c1c}
.sup{display:inline-block;border-radius:4px;padding:1px 7px;font-size:11px;white-space:nowrap}
.sup.ok{background:#dcf5e7;color:#0a7a46}
.sup.part{background:#fdf0d5;color:#8a5a00}
.sup.other{background:#eceff4;color:#555}
.sup.blocked{background:#ffe3e3;color:#9b1c1c}
.sup.na{background:#f4f4f4;color:#999}
.hops{font-family:ui-monospace,Menlo,monospace;font-size:12px;text-align:center;color:#333}
.note{font-size:11.5px;color:#777;max-width:36ch;display:block;margin-top:3px}
.path{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;color:#999;display:block;margin-top:3px}
"""

SCRIPT = """
const R=document.getElementById('r'),T=document.getElementById('t'),S=document.getElementById('s'),G=document.getElementById('g'),F=document.getElementById('f');
const rows=[...document.querySelectorAll('tbody tr')],C=document.getElementById('count');
function upd(){const q=F.value.toLowerCase(),rr=R.value,tt=T.value,ss=S.value,gg=G.value;let n=0;
for(const el of rows){const ok=(!q||el.textContent.toLowerCase().includes(q))
&&(!rr||el.dataset.route===rr)&&(!tt||el.dataset.traps.split('|').includes(tt))
&&(!ss||el.dataset.support===ss)&&(!gg||el.dataset.state===gg);
el.style.display=ok?'':'none';if(ok)n++}
C.textContent=n+' of '+rows.length+' shown'}
F.oninput=R.onchange=T.onchange=S.onchange=G.onchange=upd;upd();
"""


def esc(value) -> str:
    return html.escape(str(value or ""))


def answer_cell(row: dict) -> tuple[str, bool]:
    answer = row.get("answer") or {}
    text = answer.get("display") or answer.get("value") or ""
    refusal = row.get("answer_type") in ("refusal", "false_premise")
    return esc(text), refusal


def build(gold: list[dict], hops: dict[str, dict], measured: dict[str, dict], generated_from: str) -> str:
    routes = sorted({r.get("route") or "" for r in gold} - {""})
    traps = sorted({t for r in gold for t in (r.get("traps") or [])})
    supports = [s for s in ("yes", "partial", "no", "blocked", "n/a")
                if any(h.get("graph_support") == s for h in hops.values())]

    counts = {}
    for h in hops.values():
        counts[h.get("graph_support")] = counts.get(h.get("graph_support"), 0) + 1
    summary = ", ".join(
        f"{counts[s]} {SUPPORT_LABEL.get(s, (s, ''))[0]}" for s in supports if s in counts
    )

    state_counts = {}
    for m in measured.values():
        state_counts[m.get("state")] = state_counts.get(m.get("state"), 0) + 1
    measured_summary = ", ".join(
        f"{state_counts[k]} {v[0]}" for k, v in STATE_LABEL.items() if state_counts.get(k)
    ) or "not measured"

    out = [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        "<meta name=viewport content=\"width=device-width,initial-scale=1\">",
        "<meta name=robots content=\"noindex,nofollow\">",
        f"<title>Gold Set &mdash; {len(gold)} questions</title>",
        f"<style>{CSS}</style>",
        "<header>",
        f"<h1>Gold Set &mdash; {len(gold)} evaluation questions, and which arm answers each</h1>",
        "<div class=sub>KPMG &times; Columbia QMSS. Ten companies, Data database snapshot "
        f"2026-11-03. Generated from <code>{esc(generated_from)}</code> and GraphRAG&rsquo;s "
        "<code>hops</code> derivation; those files are the source of truth, not this page. "
        "Green = expected answer, orange = the correct response is a refusal or a rejected "
        f"premise.<br><strong>Today:</strong> {measured_summary}. "
        f"<strong>Ontology:</strong> {summary}. "
        "<em>hops</em> counts edges on the GraphRAG ontology "
        "(Company &rarr; Filing &rarr; Section &rarr; Claim). A question answered from XBRL "
        "facts has no hop count, because the graph holds no facts.</div>",
        "<input id=f placeholder=\"filter text...\" size=26>",
        "<select id=r><option value=\"\">all routes</option>"
        + "".join(f"<option value=\"{esc(r)}\">{esc(r)}" for r in routes) + "</select>",
        "<select id=g><option value=\"\">can we answer it?</option>"
        + "".join(f"<option value=\"{esc(k)}\">{esc(v[0])}"
                  for k, v in STATE_LABEL.items() if any(x.get("state") == k for x in measured.values()))
        + "</select>",
        "<select id=s><option value=\"\">all coverage</option>"
        + "".join(f"<option value=\"{esc(s)}\">{esc(SUPPORT_LABEL.get(s,(s,''))[0])}"
                  for s in supports) + "</select>",
        "<select id=t><option value=\"\">all traps</option>"
        + "".join(f"<option value=\"{esc(t)}\">{esc(t)}" for t in traps) + "</select>",
        "<span id=count></span>",
        "</header>",
        "<table><thead><tr><th>qid<th>company<th>route<th>question<th>expected answer"
        "<th>can we answer it<th>graph coverage<th>hops<th>why / path<th>traps</tr></thead><tbody>",
    ]

    for row in gold:
        qid = row["qid"]
        h = hops.get(qid, {})
        support = h.get("graph_support") or ""
        label, css = SUPPORT_LABEL.get(support, (support, "other"))
        answer, refusal = answer_cell(row)
        tickers = ", ".join(row.get("tickers") or [])
        row_traps = row.get("traps") or []

        m = measured.get(qid, {})
        state = m.get("state") or ""
        state_label, state_css = STATE_LABEL.get(state, ("not measured", "na"))
        note = esc(h.get("graph_note"))
        path = esc(h.get("graph_path"))
        reason = esc(m.get("reason"))
        cells = [
            f"<td class=qid>{esc(qid)}",
            f"<td>{esc(tickers)}",
            f"<td><span class=\"tag route\">{esc(row.get('route'))}</span>",
            f"<td class=q>{esc(row.get('question'))}",
            f"<td class=\"a{' ref' if refusal else ''}\">{answer}",
            f"<td><span class=\"sup {state_css}\">{esc(state_label)}</span>"
            + (f"<span class=note>{reason}</span>" if reason else ""),
            f"<td><span class=\"sup {css}\">{esc(label)}</span>",
            f"<td class=hops>{esc(h.get('hops')) or '&mdash;'}",
            "<td>" + (f"<span class=note>{note}</span>" if note else "")
            + (f"<span class=path>{path}</span>" if path else ""),
            "<td>" + "".join(f"<span class=\"tag trap\">{esc(t)}</span>" for t in row_traps),
        ]
        out.append(
            f"<tr data-route=\"{esc(row.get('route'))}\" "
            f"data-support=\"{esc(support)}\" data-state=\"{esc(state)}\" "
            f"data-traps=\"{esc('|'.join(row_traps))}\">"
            + "".join(cells)
            + "</tr>"
        )

    out.append("</tbody></table>")
    out.append(f"<script>{SCRIPT}</script>")
    out.append("</html>")
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gold", required=True)
    p.add_argument("--hops", required=True)
    p.add_argument("--measured", help="answerability.csv from measure_answerability.py")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    gold_path = Path(args.gold).expanduser()
    with open(gold_path) as fh:
        gold = [json.loads(line) for line in fh if line.strip()]

    with open(Path(args.hops).expanduser()) as fh:
        hops = {r["qid"]: r for r in csv.DictReader(fh)}

    measured = {}
    if args.measured:
        with open(Path(args.measured).expanduser()) as fh:
            measured = {r["qid"]: r for r in csv.DictReader(fh)}

    missing = [r["qid"] for r in gold if r["qid"] not in hops]
    if missing:
        print(f"warning: {len(missing)} questions have no hops row, e.g. {missing[:3]}")

    page = build(gold, hops, measured, gold_path.name)
    out = Path(args.output).expanduser()
    out.write_text(page)
    print(f"{len(gold)} questions -> {out} ({len(page)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
