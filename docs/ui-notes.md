# What a citation has to carry — for the UI team

Written 2026-10-26. Previously a section inside [agent-notes.md](./agent-notes.md),
which was addressed to Agents, so it had no route to you.

## The short version

Three tools answer questions, and they do not cite the same way. If a citation renders
from the five fields RAG's v0 settled on, two of the three cite at document level only —
the analyst sees *which filing* supports a claim but not *which part of it*.

We are asking for three fields. None of them changes anything RAG already shipped.

## Why ours are different

Exactly one provenance key is populated on any piece of evidence:

| Tool | Key it fills | What it points at |
| --- | --- | --- |
| Vector, RAG's | `chunk_id` | A chunk of text |
| `graph_search`, ours | `section_id` | A leaf section — a specific note or sub-item |
| `structured_search`, ours | `metadata.fact_id` | One tagged XBRL fact |

RAG's §13 settled the citation shape as `company_name`, `form`,
`reporting_period_end`, `section`, `source_url`. That is right for chunk evidence. It
has no room for a section or a fact, so a graph-derived or XBRL-derived claim can only
say "Microsoft, 10-K, FY2025".

That is the difference between an auditable answer and a plausible one. Our whole
argument for the graph rests on every claim tracing to the text that supports it; if the
citation cannot express it, the property exists and nobody can see it.

## The `section` field is coarser than it looks

This one is easy to miss, and it is not RAG's fault — it is how filings are built.

Notes are **inside** the Item structure. In a 10-Q, Part I Item 1 *is* "Financial
Statements", and all twenty-odd notes live within it. So `section` does not return
nothing for Note 16. It returns `"Part I Item 1"` — the same value it returns for the
income statement, the balance sheet, and every other note.

That is worse than a blank, because a blank announces itself and a correct-but-useless
label does not. It is also over 100,000 tokens of filing behind one label.

## What we are asking for

| Field | Example | What it gives you |
| --- | --- | --- |
| `section_path` | `"Part I Item 1 > Note 16 — Segment Information and Geographic Data"` | A readable breadcrumb instead of a label shared by a hundred pages |
| `section_id` | hash of the leaf | A stable citation target. Survives re-chunking, which `chunk_id` explicitly does not |
| `metadata.fact_id` | — | Lets a number cite the tagged fact behind it, not just the document |

`section` itself stays exactly as RAG defined it. Their prefix filtering is untouched,
nothing of theirs breaks, and their §6 says additive metadata keys need no new contract
version.

**Worth saying plainly:** our own data contract had `section_path` in it, as
`"Item 7 > Liquidity and Capital Resources"`. Conforming to RAG's flat `section` is what
dropped it. This is us asking for something back, not pointing at a gap in their work.

## What we are not asking for

- No change to `section`, to the filter behaviour, or to anything RAG shipped
- No new contract version — all three are additive
- Not for UI to learn our schema. `section_path` is a string you render; `section_id` and
  `fact_id` are opaque values you carry through and pass back

## Two questions before this becomes a request

1. **Is the citation renderer built yet?** If not, this is a design input and costs
   nothing. If it is, we would rather know what it would take than assume.
2. **Who renders the citation — UI or Agents?** Agents own the chat API that feeds you,
   so the field may have to survive two hops. If it is dropped in the middle, adding it
   at the edges does not help.

## When

INT-2 on 2 Nov is the thin end-to-end demo and the first time UI renders graph evidence.
Before that this is cheap. After it, it is rework.

---

Related, if useful: [section-vocabulary.md](./section-vocabulary.md) has the full
three-field proposal and why each team needs a different granularity —
[architecture.md §7](./architecture.md#7--tool-signature-and-evidence-shape) has the
evidence shape.
