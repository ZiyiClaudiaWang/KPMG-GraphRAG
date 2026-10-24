# Subsidiary pairs — fuzzy entity-resolution ground truth v1

Owner: Alina (Extraction 2). 18 Oct 2026.

**What this file is for:** an answer key for testing fuzzy matching of subsidiary names. Run the matcher on these 50 pairs, compare its output with `label`, and report precision and recall. That turns the W5 fuzzy-ER spot-check into a measurement instead of a judgement call.

**What this file is not:** loader input. The graph loader must never read this file. The pairs are the test, so feeding them into the graph and then scoring the graph against them measures nothing. `relationship_type` describes what the *correct* graph looks like for these pairs, so the matcher's output can be checked against it.

## Labelling rule

**yes**: the two names refer to the **same legal entity**. That covers renames, formatting or legal-suffix changes ("X" → "X Limited", "PLC" → "plc"), and a short name the 10-K defines for the entity ("JPMSE" = J.P. Morgan SE).

**no**: anything else, including:
- **a product, brand or business line vs a company.** A product is not a company. They may be linked by a relationship, but never merged into one node ("LinkedIn" as a business line ≠ LinkedIn Corporation);
- a parent vs its subsidiary, or two subsidiaries of the same group;
- **a company merged into another.** When A merges into B, A becomes part of B and ceases to exist, so A ≠ B;
- **a conversion that also absorbs other businesses.** The old entity is kept as the predecessor (J.P. Morgan AG → SE).

**Acquisition is not a merger-into.** When Microsoft acquired LinkedIn, Nuance and Activision Blizzard, each became a subsidiary and kept existing as its own entity, which is why they appear in Exhibit 21. An acquisition changes no label in this file.

## relationship_type and effective_year

Filled in only where a filing supports it. A blank cell means not verified, not "no relationship". The `evidence` column cites the source.

| Value | Meaning | Pairs | Label |
| --- | --- | --- | --- |
| `identical` | Same legal entity | 1–14, 16, 17, 19, 20, 50 | yes |
| `brand_or_product_of` | A is a product, brand or business line of B | 22, 24 | no |
| `predecessor_to` | A converted into B and B absorbed other businesses in the same step | 48 | no |
| `absorbed_into` | A merged into B and ceased to exist | 47, 49 | no |
| `parent_of` | A directly holds B, as stated in the 10-K | 25, 26 | no |
| `co_subsidiary` | Different entities in the same group; ownership between them not stated unless cited | 15, 18, 21, 27–46 | no |
| blank | Not verified | 23 | no |

`effective_year` is the calendar year of the event, filled only where verified: 47 = 2019 (still in Ex21 filed Feb 2019; merger reported in the FY2019 10-K), 48 and 49 = 2022 (announced 24 Jan 2022).

Pairs 38 (JPMorgan Asset Management (UK) vs its Holdings (UK)) and 46 (Broker-Dealer Holdings vs J.P. Morgan Securities LLC) look like parent and child from their names, but no filing states it, so both stay `co_subsidiary`. Pair 43 is verified as siblings: the parent holds both JPMorgan Chase Bank, N.A. and JPMorgan Chase Holdings LLC directly.

## How this maps to the agreed ontology (PR #10)

Follows the ontology in `docs/ontology-reconciliation.md`: PascalCase nodes, UPPER_SNAKE relationships.

| relationship_type | In the graph |
| --- | --- |
| `identical` | One `Subsidiary` node (2.6 `sub_id`). Each name variant kept as provenance |
| `parent_of` | Existing `OWNS` relationship |
| `co_subsidiary` | Separate nodes, each linked to the parent by `OWNS`. No edge between siblings |
| `absorbed_into`, `predecessor_to` | Separate nodes. Edge not in v1. Candidate `ABSORBED_INTO` / `PREDECESSOR_OF`, to propose in the ontology file with the questions they answer (M07, M08) |
| `brand_or_product_of` | No Product node in v1. The mention must not be resolved to the company. Revisit if a question needs products |

Registrants (`Company`) keep one node across renames with valid-from and valid-to dates (schema-ids-operations.md, M07). The same applies here: a plain rename or re-domicile is `identical` (pair 50). Only pair 48 is split, because the change also merged in other businesses.

## Proposed matcher rules (for the matcher, with Alison; not part of the answer key)

1. **Legal-form block.** If *both* names carry a legal form and the forms differ after normalisation (LLC vs plc vs S.à r.l. vs Inc.), never match, however similar the rest is. A missing suffix on one side is not a block (pair 14 is yes).
2. **Jurisdiction block.** If both records have a jurisdiction from the Exhibit 21 column and they differ, never match. Use the column, not a country word guessed from the name.
3. **Abbreviation table** before comparing: "N.A." = "National Association", "plc" = "PLC", and so on (pairs 13, 16).
4. **Defined terms first.** If the 10-K defines a short name ("J.P. Morgan Securities" = J.P. Morgan Securities LLC), use that definition before any similarity score (pairs 17, 18).

Known exception: pair 50 (Corporation → Pte Ltd) is labelled yes but rules 1–2 would block it. That is accepted, and it should show up as one false negative when the rules are tested.

## Decision log (Alina)

| Pair | Names | Label | Decision |
| --- | --- | --- | --- |
| 47 | Chase Bank USA ↔ JPMorgan Chase Bank | no | Merged into JPMCB in 2019 |
| 49 | J.P. Morgan Bank Luxembourg ↔ J.P. Morgan SE | no | Merged into SE, Jan 2022 |
| 48 | J.P. Morgan AG ↔ J.P. Morgan SE | no | 18 Oct, changed from yes: conversion plus merger, kept as predecessor |
| 50 | Microsoft Regional Sales Corporation ↔ … Pte Ltd | yes | Same entity re-domiciled (explanation not verified) |
| 22 | "LinkedIn" ↔ LinkedIn Corporation | no | 18 Oct, changed from yes: business line, not a company |
| 24 | "Nuance Healthcare cloud services" ↔ Nuance Communications | no | 18 Oct: product, not a company |
| 6 | Microsoft Ireland Research ↔ … Unlimited Company | yes | Suffix added |
| 12 | Apple Operations ↔ Apple Operations Limited | yes | "Limited" added in the same year as Apple's other Irish entities |

The other 42 labels were drafted with AI assistance and reviewed by Alina.

## Pair types and totals

E identical name across years (5) · A suffix or format change (9) · B 10-K text vs Exhibit 21 (11) · C similar names, different entities (21) · D merger, conversion, re-domicile (4).
Totals: **19 yes, 31 no.**

## Sources

Exhibit 21 from each company's 10-Ks on SEC EDGAR: MSFT FY2017–FY2026, JPM FY2016–FY2025, AAPL FY2016–FY2025. "FY" is the fiscal year the 10-K covers; JPM files in February for the prior year. Text mentions and parent and sibling evidence come from MSFT 0000950170-25-100235 and JPM 0001628280-26-008131 (`docs/pilot-filings.md`); merger events come from the JPM FY2019 and FY2021 10-Ks.

## Known limits

- Exhibit 21 lists only *significant* subsidiaries. A name that drops off may still exist (Nuance after FY2024).
- Pairs 8–12 (Apple Irish entities gaining "Limited") are labelled yes on timing. If any turns out to be a legal-form conversion rather than a naming change, revisit it against the rule for pair 48.
- MSFT (8 names) and AAPL (about 19) have short lists, so JPM supplies most type C pairs.
