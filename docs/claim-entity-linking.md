# Linking Claims to subsidiaries and segments

18 Nov 2026. Covers README "Not built yet" 3.2. Numbers are from
`runs/2026-11_20filings_label-v2` (20 filings, gpt-4.1-mini, `label_v2`).

## 1. What step 5 does today with a sentence that names a subsidiary or segment

Step 5 (`src/label.py`, prompt `prompts/label_v2.txt`) asks the model, per sentence, for
`entity_mentions`: `mention_text`, `entity_type` (segment / subsidiary / unknown),
`reference_kind` (named / generic / uncertain) and an `evidence_quote`. The model never sees a
list of the company's subsidiaries or segments. Step 7b (`entity_annotation.annotate_mentions`)
then checks the quote and tries to match the mention to `data/entity_catalog.json`.

What came out of the 20-filing run:

| | Count |
|---|---|
| Units labelled | 15,053 |
| Units with at least one mention | 1,176 |
| Mentions returned by the model | 1,670 |
| … `unknown` / `uncertain` | 721 |
| … `unknown` / `generic` | 502 |
| … `segment` / `named` | 262 |
| … `subsidiary` / `named` | 100 |
| … `unknown` / `named` | 73 |
| Mentions on Claims, matched to an entity | **0** |
| Claims with a non-empty `related_subsidiary_ids` / `related_segment_ids` | **0** |

### Why nothing links: four gates, three of them in code

1. **The catalog is empty.** Step 7b reads `data/entity_catalog.json`, which does not exist.
   Step 1b already writes the Exhibit 21 list in the same format
   (`data/output/subsidiary_catalog.json`), but nothing reads it. There is no segment catalog
   at all.
2. **Every catalog entry would be `needs_review`.** Even when the catalog is filled, an Exhibit
   21 hit can only reach `candidate_match_needs_review`, never `matched`.
3. **`build_claims.py` clears the links anyway** (lines 111–114: `related_*_ids=[]`,
   `matched_entity_id=None`, "no confirmed entity links in this unreviewed run").
4. **Nothing loads them.** `load.py` writes no Claim → Subsidiary/Segment edge, and the schema
   (`schema-ids-operations.md`) has no such edge.

### Where the matching logic itself loses mentions

- **Cue word required.** A named mention needs "segment" or "subsidiary/wholly owned" in the
  quote. 126 named segment and 82 named subsidiary mentions stop at
  `needs_type_evidence_review` for this reason. Most sentences that mention a segment or subsidiary
  just use its name ("Intelligent Cloud revenue increased…", "LinkedIn revenue…").
- **Exact, case-folded name match only.** "Rockstar Games" will not match Exhibit 21's
  "Rockstar Games, Inc."; "Medical-Surgical Solutions segment" (two hyphen characters appear in
  the run) will not match "Medical-Surgical Solutions".
- **`unknown` is never matched.** 794 `unknown` mentions (named or uncertain) go to review and stop there,
  even when the name is in the catalog.

### Where the model itself is the problem (the prompt question)

- **Recall on subsidiaries named without a cue is low**, because the prompt asks for
  "explicit references". Sentences that are Claims containing the name, and how many got a mention:
  LinkedIn 17 → 5 (none as subsidiary), GitHub 4 → 1, Nuance 1 → 0, MiniMed 22 → 15 (5 as
  subsidiary).
- **Same name, different type.** Converse: 36 sentences, tagged 6× subsidiary, 7× segment,
  11× unknown. Core Ventures and Medtronic Luxco also appear under three different type and kind combinations. (Converse is
  in fact both a Nike subsidiary and a reportable segment; the schema should allow that.)
- **The registrant tagged as an entity.** "Medtronic plc" ×9 as a named subsidiary; "Microsoft"
  and "NetApp" ×6 each as unknown. The prompt excludes "the Company / we / our" but not
  the registrant's name.
- **Counterparties tagged.** OpenAI ×9, Google ×7, OECD, FDA, CVS as `unknown`.

**Verdict:** the prompt can be improved, but it is not why there are no links. The model cannot know
what is in Exhibit 21 or the XBRL segment list; matching names against those lists is the
code's job. Fix the code first. It is free, because the catalog is not part of the label
request hash (`request_key()`), so no relabel is needed. Change the prompt in the relabel the
233-filing corpus needs anyway.

## 2. Proposed change

### P1: code only, no LLM cost, testable on the 20 filings today

1. **Build `data/entity_catalog.json`** from `subsidiary_catalog.json` (step 1b) plus a
   segment catalog from XBRL (section 4).
2. **Match on keys, not raw strings:** normalise Unicode dashes, strip a trailing
   "segment(s)" for segments, compare on `entity_key` (legal form stripped) for subsidiaries.
3. **A catalog hit is type evidence.** Keep the cue-word rule only for mentions that hit no catalog
   entry. Let `unknown` mentions match too; the catalog decides the type.
4. **Dictionary pass in code:** scan every Claim for the filing's catalog names and add a
   mention with `origin: dictionary`. This catches what the model skipped (LinkedIn). Guard
   list: segment names that are also places or common words ("Europe", "Japan", "Services",
   "Americas") need the word "segment" in the sentence, or a segment section, to count.
5. **Review policy (team decision):** an exact `entity_key` hit on an Exhibit 21 or XBRL
   source becomes `matched_auto`; anything fuzzier stays `candidate_match_needs_review`. Remove
   the override in `build_claims.py` for `matched_auto`.
6. **Measure before and after:** matched count, plus precision on 50 sampled links checked by
   hand.

### P2: prompt `label_v3`, shipped with the 233-filing relabel

- Add two input fields: `registrant` (name and ticker), and `known_entities`, which holds the
  filing's segments plus the catalog subsidiaries whose `entity_key` appears in this batch
  (code pre-filters, so Microsoft's long Exhibit 21 never enters the prompt whole).
- Rules: the registrant is never a mention; regulators, customers, partners, investees and
  lenders are not subsidiaries; when a name is in `known_entities`, use its type; one name may
  be both a subsidiary and a segment.
- Re-score the 20 filings with the same checks as P1.6 before the full run. Rough cost of the
  full relabel at the run's rate ($1.81 for 20 filings, 10 of them 10-Ks): about $15–25.

### P3: load, which needs a contract decision (Adeline)

`(Claim)-[:MENTIONS {mention_text, match_status, origin}]->(Subsidiary|Segment)`, loaded for
`matched` / `matched_auto` only. This is a new edge, so it goes into `schema-ids-operations.md`
before `load.py` writes it.

## 3. Subsidiaries at 233 filings, across years

`config/filings.yaml` now has 233 filings: 59 10-Ks and 174 10-Qs, about 6 years per company. Only
10-Ks carry Exhibit 21, so step 1b downloads 59 exhibits (no LLM; needs `SEC_USER_AGENT`).

What already works across years: `sub_id` = parent CIK + normalised name, so the same legal
entity in 2021 and 2025 is one node. `OWNS` is keyed by accession, so each year that lists it
adds an edge, and the year is on the edge.

What breaks at more than one year:

1. **Scope is too wide.** `catalog()` gives every subsidiary *all* of the parent's accessions,
   so a 2021 10-Q Claim could link to a company bought in 2024. Fix: each filing gets a
   *reference 10-K*, the first 10-K whose `reporting_period_end` is on or after the filing's
   (the same fiscal year), or the latest 10-K for 10-Qs after it. A subsidiary is in scope
   when that 10-K lists it. If only the year before lists it, it stays in scope with the flag
   `listed_prior_year_only` (to cover mid-year sales).
2. **Renames.** A legal-form change (Inc. → LLC) gives a new `sub_id`. Report pairs with the
   same `entity_key` where one disappears and the other appears in the next year as rename
   candidates in `subsidiary_qa.csv`; do not merge them automatically.
3. **Missing or odd exhibits.** A year with no EX-21 attached, or a jump in `rows_parsed`
   between years, is a CHECK row. A missing year falls back to the nearest year, with a flag.
4. **Idempotent reload.** Run step 8 twice at 233 filings and check that node and edge counts do not
   change (this is also the v2 "Done when").

## 4. Segments: where the list comes from

| Option | Coverage | Cross-year | Cost | Problems |
|---|---|---|---|---|
| **XBRL segment members** (`structured/segments.py`, `StatementBusinessSegmentsAxis`) | Every filer that tags segments | Per accession, so reorganisations show up | Free | Labels come from QNames ("CDBU"), so prose names need aliases |
| Regex on Item 1 (`source_review.py`) | AAPL and MSFT only | Per filing | Free | Two hand-written patterns |
| LLM pass over Item 1 / the segment note | Any | Per filing | One small call per filing | 10-Q segment note sits inside Item 1 (not addressable, `data-contract.md`); output needs validation |

**Recommendation:** the XBRL members are the segment list for each accession. A small LLM pass
over Item 1 (10-K) supplies only aliases and descriptions ("Mobile and Client Business Unit"
→ `mu:MCBUMember`), and any name it returns that maps to no XBRL member is flagged, not added.
The same pass can later collect the quotes `CONSOLIDATED_INTO` needs (E14).
Segment id stays `hash(cik + normalised_name)`. A name that stays the same while the segment's
contents change is one node, so `HAS_SEGMENT` should carry the accession, like `OWNS`.

To check first: does `financial_facts_v1` have segment members for all 233 accessions, and do
the members of each company change between its older and newer filings?

## 5. Limitations

- **Exhibit 21 is not a full list.** Filers may leave out insignificant subsidiaries, so a
  mention with no catalog hit is not proof that the entity is not a subsidiary, and counts are
  not totals.
- **Brand vs legal entity.** Prose names brands ("Rockstar Games", "LinkedIn"); Exhibit 21
  names legal entities. Some brands will have no Exhibit 21 row at all, and will need an alias
  or stay unlinked.
- **Direct vs indirect ownership** is unknown unless the exhibit nests its rows.
- **Exhibit 21 has no segments**, so `CONSOLIDATED_INTO` (E14) cannot come from it.
- **Loading the edge is not enough for retrieval.** `graph_search` reaches Claims through the
  full-text index and never walks `OWNS` (`neo4j-instances.md`), so it will not walk a
  `MENTIONS` edge either. Using the edge needs the Text2Cypher arm or a change to
  `graph_search`.
- **Exhibit 21 names alone link few Claims (measured 18 Nov on the Aura instance holding
  10,774 Claims and 752 Subsidiaries).** Matched against the parent's own Claims:
  - full legal name (`name`) appears in a Claim for **10 of 752** subsidiaries (28 Claims);
  - core name (`entity_key`, legal form stripped, at least 4 characters) for **43** subsidiaries,
    on 286 Claims. This finds the brands (linkedin, activision blizzard, rockstar games, zynga,
    2k games, converse, minimed group, covidien, cathworks).
  - The core-name count is inflated by plain substring matching: keys equal to the registrant
    ("netapp" on 90 NTAP Claims, "medtronic", "apple") and short words inside other words
    ("lord" in "landlord"). P1 needs word boundaries and must drop keys equal to the
    registrant's own name.
- **Not yet measured:** whether `financial_facts_v1` has segment members for all 233
  accessions.
