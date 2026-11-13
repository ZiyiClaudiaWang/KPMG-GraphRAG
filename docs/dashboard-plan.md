# Corpus dashboard — plan

Status: proposal, not built. Written 13 Nov 2026 for review before we start.
Owner: Adeline. Reviewer: Kelly.

## The problem 

Our failures this month have mostly not been bad retrieval. They have been
nobody being able to see what is loaded.

- The live graph has **no Subsidiary nodes**. The Exhibit 21 pipeline merged in
  #75 on 12 Nov, but the load has never been run against the instance this
  repo's `.env` points at. Checked by hand today: five labels, `Subsidiary`
  absent, counts byte-identical to 11 Nov. Nine Exhibit 21 questions sit behind
  that one run, and the only place it was written down was a handover note.
- Two fact stores on one laptop hold **21,505** and **24,533** facts, and you
  told them apart by the *absence* of a key in `store_meta_v1`. The EDGAR store
  is deprecated as of 13 Nov and the data-API store is what we build on, which
  settles that one — but only for people who know.
- The tracked DuckDB in #55 was the silent default store and held 1 filing /
  1,429 facts. Every structured query against it raised.
- Nine of twenty filings failed to load once because an empty first filing
  typed every column NULL. Corpus totals looked fine throughout.

At 20 filings these are findable by hand. We are going to 50, then hundreds.
None of them are findable by hand at that size.

## What it is, and what it is not

**Is:** a team-local page. Everyone exports their own environment and runs it.
No hosting, no shared credentials, nothing client-facing. If KPMG ever sees it
that is a separate decision with a separate credentials answer.

**Is not:** a benchmark. Eval owns scoring, and their rule that a team should
not grade its arm on a benchmark it wrote is right. Everything here is a
regression harness and a legibility tool. We will say that to them in those
words.

**Is not:** a replacement for browsing. DuckDB already browses facts fine. The
tab we are *not* building is a generic table viewer.

## Shape

Three consumers, one implementation:

```
tools/corpus_report.py   counts, checks, provenance -> JSON + printed table
      |                  (no streamlit import, importable from CI and the deck)
      +--> pytest        the checks become assertions
      +--> streamlit     tools/dashboard/app.py renders the cached JSON
      +--> build_deck    stops hand-typing figures into slides
```

Connection goes through `graphrag/backends.py` so "which arm is configured" has
one source of truth and does not drift from what retrieval actually does.

Refresh is a button, never page load. Aura cold-starts: the first call after
idle raises `RetrievalUnavailableError`, so the app pre-warms with
`verify_connectivity()` or it looks broken every morning.

## Two graph instances, and which one this reads

Settled 13 Nov:

- **Instance 1 is canonical.** It is what we give Eval and Agents, so its
  contents are a published artifact and should change deliberately. Credentials
  stay where they are, in `extraction_pipeline/.env`.
- **Instance 2 is the team's internal graph**, and Kelly has already run the
  Exhibit 21 load against it. Credentials go in a new git-ignored
  `.env.internal`.

**The dashboard reads instance 2 by default**, because the point is to see our
own numbers before anything is published. It takes the instance as a selector,
not a connection detail — `--instance internal` or `--instance canonical` — and
the instance name is part of the provenance line on every page and every saved
run. Comparing the two is then a first-class thing the tool can do, and
"canonical is missing what internal has" becomes visible rather than tribal
knowledge.

Instance 1 has no subsidiaries yet, so the nine Exhibit 21 questions stay
unscoreable until it gets them — this is what Eval has asked for twice. The
load is additive and verified: 752 nodes, 752 edges, nothing else moves.
Sequencing it is an open item, not a blocker.

## Scope is a filter, not a footnote

`pipeline_scope` is a property the loader stamps on every node and edge it
writes. Instance 2 carries one, `kelly-extraction-pilot-v1`, on all 11,614
nodes. It is what `--wipe` deletes, so a wipe is scoped rather than a
whole-database clear, and it is what lets two extraction runs coexist in one
database.

**Every count filters by scope.** The selector defaults to the only scope
present, and the page shows a loud banner when there is more than one. Counting
unfiltered works today and breaks silently the first time someone loads a v2
extraction beside v1: Claim 10,774 becomes about 21,000 with nothing explaining
why, and orphan checks start reading two parallel runs as broken structure.
That is precisely the moment you would most want to trust the page.

## The provenance line

This matters more than the counts and goes at the top of every page and every
saved run: graph instance, `pipeline_scope` and the count of distinct scopes in
the database, then store path, `corpus_version`, `source`, `loader_version`.

Most of this month's confusion was two correct stores, not one wrong one. With
two graph instances in play as well, no number on this page means anything
without the line that says where it came from. The `source` value belongs in
full: the store we are moving to reads `data-api:raw`, which is the unvalidated
set, and a page that shows a number without that word is making a stronger
claim than the data supports.

## Metrics by arm

The short version of what gets tracked, so it is clear what each arm is held to.
Detail and rationale follow below.

### Graph arm (ours)

| Metric | What it catches |
| --- | --- |
| Nodes by label, relationships by type | The baseline. Today: Claim 10,774, Section 48, Filing 20, Company 10, RiskFactor 10; SUPPORTS 10,774, CONCERNS_RISK 6,198, CONTAINS 48, FILES 20 |
| Subsidiary coverage | 10-Ks with an Exhibit 21 against 10-Ks with Subsidiary nodes, and subsidiaries per parent. Instance 1 reads 10 and 0 |
| Distinct `pipeline_scope` | More than one means two runs are superimposed in one database |
| Orphans | Claims with no SUPPORTS, Sections with no Claims, Filings with no Sections, RiskFactors with no CONCERNS_RISK, Subsidiaries with no OWNS |
| Claims per section, min / median / max | A section with 400 claims beside one with 2 means the extractor behaved differently. At scale this is where recall dies |
| Claims per filing | A filing that extracted thin |
| Claims per risk theme | Whether the ten-theme library is evenly used or collapsing into two buckets |
| Companies missing a ticker or name | Predicts answering the wrong company |

### Structured arm (ours)

| Metric | What it catches |
| --- | --- |
| Filings, companies, facts | The baseline. Today: 20 / 10 / 24,533 in the data-API store |
| Facts, concepts and periods per filing | A filing that loaded thin or broke. Totals hide this |
| Key-concept presence matrix | Whether every filing actually has revenue, net income, assets, cash, operating income |
| Consolidated against dimensioned, per concept | Both live ranking bugs are segment-shaped |
| Axes present, members per axis | Which segment dimensions exist at all |
| Duplicate `fact_id` | A loader bug. Should be zero |
| Comparative pairs, and restatement pairs where the value differs | Expected duplication against real restatements. Nobody has looked at the second |
| Statement duplicates | Same concept, period and dimensions with more than one `context_ref` in one filing |
| Four date checks | Start after end, duration typed instant, period outside the fiscal window, period length not matching the form |
| Order-of-magnitude outliers per concept | Nike revenue coming back 50x small |
| Column population per field | Already found three all-NULL columns in the new store: `sign`, `scale`, `format`. `numeric_value` 87% against EDGAR's 93%, `unit` 87% against 100% |
| Nil facts, unit and currency mix, scale spread | Type and unit damage. Every column is still VARCHAR, so #37 does not close with the source switch |
| Company-name variants per CIK, ticker null rate | `Microsoft Corporation` against `MICROSOFT CORP`; ER recall is 0.789 |
| Pass rate on the three oracles | Accounting identity, segment sums, quarterly roll-up |

### Both arms — ranking

| Metric | What it catches |
| --- | --- |
| Rank-1 stability across `top_k` | Rank 1 depending on `top_k`, which has happened |
| Repeat-run identity | A backend that does not sort deterministically, which is a silent bug today |
| Paraphrase and period sensitivity | "top line" returning a street address; the ranker reading no periods |
| Latency per arm, including first call after idle | Aura cold-starts |

### RAG arm (not ours)

| Metric | What it catches |
| --- | --- |
| Chunks per accession | A filing present in one arm and missing from another, which makes eval scores incomparable |

Their index is in another repo and we cannot reach it, so this column stays
empty until we need it. We are not asking them for a counts hook yet.

## Indicators

### Built first — each one would have caught something real

**Per-filing rows, not corpus totals.** One row per accession: facts, distinct
concepts, distinct periods, dimensioned share, held for review, Claims in the
graph, subsidiaries. Totals hide a broken filing; the empty-first-filing bug
was a per-filing outlier that no total showed.

**Which store, and built from what.** The EDGAR loader is deprecated as of
13 Nov; the structured arm builds on the data-API store from here. One
consequence worth stating rather than quietly dropping: **the load funnel goes
with it.** Raw 23,506 to review 50 to canonical 21,505 was only visible because
the EDGAR loader wrote `facts_raw_v1` and `facts_review_v1`. The data-API store
writes three tables, so there is no raw layer to compare against and no review
hold. We are dropping the funnel rather than asking the loader to persist a raw
layer this week, which means the "about 2,000 facts go missing" question is
abandoned, not answered. Worth revisiting if facts start disappearing at 200
filings.

**Key-concept presence matrix.** Revenues, NetIncomeLoss, Assets,
CashAndCashEquivalents, OperatingIncomeLoss crossed with every filing, green or
red. "Does every filing have revenue" is the first question a client asks and
the cheapest smoke test we have.

**Cross-arm coverage, keyed on accession.** Facts in DuckDB, Claims in the
graph, chunks in RAG. A filing present in one arm and missing from another
makes eval scores incomparable and nothing surfaces it today. RAG's column
stays empty until we need it — we are not asking them for a counts hook yet.

**Graph structure.** Counts by label and relationship type. Orphans: Claims
with no SUPPORTS, Sections with no Claims, RiskFactors with no CONCERNS_RISK.
Claims-per-section spread — a section with 400 claims beside one with 2 means
the extractor behaved differently on them, and at scale that is where recall
dies.

### Data quality

**Subsidiaries as coverage, not count.** Filings that have an Exhibit 21 versus
filings with Subsidiary nodes. Today that reads 10 and 0.

**Duplicates, split in two.** These are unrelated things wearing one word.
Same `fact_id` twice is a loader bug and should be zero. The same economic fact
across two accessions is a *comparative* — FY2025 figures living inside the
FY2026 10-K — which is expected, is why the gold set's `fact_id`s resolve 0/10,
and must not be deduped away. Reported separately, because at 200 filings
comparatives will swamp any combined count. The interesting panel is
comparative pairs whose **value differs** across filings: those are restatements
and nobody has looked at them.

**Statement duplicates.** Same concept, period and dimensions with more than one
`context_ref` inside one accession. Expect mostly benign.

**Dates, four cheap checks.** `period_start` after `period_end`; duration facts
typed instant; `period_end` outside the filing's fiscal window; period length
not matching the form, such as a 10-Q carrying a 365-day duration. The last one
bears directly on the open period-ranking bug.

**Segments.** Axes present, members per axis, and per concept the consolidated
versus dimensioned ratio. Both live ranking bugs are segment-shaped: Microsoft
365 member slices scoring 1.05 and pushing the consolidated figure out of the
top 8, and Nike's revenue returning 50x small as a segment slice. A line reading
"Revenues: 1 consolidated, 34 dimensioned" is the diagnostic for both.

**Order-of-magnitude outliers.** Group by concept across companies, flag values
more than about two orders off the median. One query, and it is exactly the Nike
bug.

**Identity health.** Company-name variants per CIK and ticker null rate. The two
loaders already disagree — `Microsoft Corporation` against `MICROSOFT CORP` —
and entity resolution recall is 0.789. This predicts "Medtronic answers a
Microsoft question" before eval finds it.

## Observing the ranking

The tab that earns its keep. Run `graph_search` and `structured_search` through
`backends.py` and show ranked rows side by side with scores and provenance keys.
This is what we already do by hand, repeatedly — both the period-word bug and
the street-address-at-rank-1 bug were found this way.

Every query appends a JSONL row: trace id, question, filters, arm, top_k, the
ranked rows with scores, latency, and the provenance line. That gives us three
things we do not have — a record of ranking behaviour before and after a fix, a
shared place to look at the same bad ranking instead of each re-deriving it, and
reproducible evidence for the "found by running" slides.

## Deterministic tests

Most of these are the same queries as the indicators, which is why they are
cheap.

**Invariants with a real oracle** — derivable from the data, so they cost
nothing per filing added:

- Accounting identity: Assets = Liabilities + Equity per filing and period.
  XBRL ships calculation linkbases for exactly this.
- Segment sums: dimensioned members on an axis sum to the consolidated value,
  modulo eliminations.
- Quarterly roll-up: Q1 through Q4 sum to FY for duration concepts.
- Comparative consistency: FY2025 as reported in the FY2025 10-K equals the
  FY2025 comparative inside the FY2026 10-K, or it is a restatement worth
  flagging.

**Determinism of the ranking itself** — untested today, and `contract.sort_key`
returning `(-score,)` only means a backend that sorts non-deterministically is a
silent correctness bug right now:

- Rank 1 must not depend on `top_k`: the result at 5 is a prefix of the result
  at 20. This exact failure happened when the tie-break was the provenance hash.
- Repeat runs, including after a reconnect, are identical.
- Shuffling row order into the scorer does not move the ranking.
- Adding a filter may only remove rows, never reorder the survivors.

**Metamorphic tests** — no labels needed, which is why they scale:

- Paraphrase invariance: "total revenue", "revenues" and "top line" reach the
  same rank 1. This fails today — "top line" returns a street address — and it
  fails without anyone writing gold.
- Period sensitivity: swapping "three months" for "nine months" must move the
  answer. If it does not, the ranker reads no periods, which is the second
  unfixed bug.

**A fixture filing.** One hand-built filing seeded with what has bitten us: a
segment member name containing a period word, a negative value, a restated
comparative, a nil fact, a 50x scale error. Exact assertions, no network. This
is what finally gives CI coverage of `structured/`, which it has never had
without a database.

**Auto-generated gold, with a caveat.** For every consolidated fact we hold,
template "What was {company}'s {label} for {period}?" and assert the arm returns
that `fact_id`. At 200 filings that is thousands of cases for free. It only
tests what we extracted, so it measures retrieval and not extraction coverage,
and it is circular if the phrasing comes from the concept name — it needs
US-GAAP labels, which are not built. Third independent argument for building
them.

## Build order

**PR 1 — `tools/corpus_report.py`.** Counts, checks, provenance; JSON out and a
printed table. Tests in `tests/test_corpus_report.py`, with the `sys.path`
preamble every test module here carries. No streamlit import in this file. Half
a day.

**PR 2 — `tools/dashboard/app.py` and the query log.** Three tabs: Corpus,
Checks, Ask. Reads PR 1's cached JSON. `streamlit` goes in
`requirements-dev.txt` and is imported only in the app. A day or so.

**PR 3 — determinism tests.** Starting with segment sums, the `top_k` prefix
test and period sensitivity. All three fail today against known bugs, so we
watch them go green as fixes land.

Cut from scope: the browse tab.

## Settled, and still open

Settled 13 Nov: instance 1 canonical and published to Eval and Agents, instance
2 internal and read by this dashboard. Kelly has already run the Exhibit 21 load
against instance 2, so we are not running it anywhere — the question of whether
it reaches instance 1 is a publish decision and hers.

Still open, for Kelly:

1. **Are the metrics above the right ones for each arm** — anything missing,
   and anything not worth the query?
3. **One scope or several in instance 2?** If parallel scopes become normal
   internally, the dashboard should treat scope as a selector alongside
   instance, not as a fact to report.
4. **What do we want to learn from comparing the two instances?** The tool can
   diff them cheaply. Worth deciding whether that is a panel or just something
   we run before a handover to Eval.

As a side note from checking instance 1 today: our own extraction of Exhibit 21
produced 752 subsidiary rows across the 10 10-Ks, all ten parsing OK with no
unmapped jurisdictions and one duplicate name in MDT. Worth comparing against
whatever instance 2 holds — if the two numbers differ, one of us has a parser
or config difference worth knowing about.
