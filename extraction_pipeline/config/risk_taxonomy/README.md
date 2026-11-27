# Risk taxonomy

Our own two-level risk themes for Item 1A, read by step 4c (`src/risk_themes.py`, the
classifier), step 7a (`src/risk_factors.py`) and step 8 (`src/load.py`). They replaced the 10
themes of the old `config/risk_themes.csv` / `risk_theme_map.csv` (26 Nov 2026).

| File | What it holds |
|---|---|
| `groups.csv` | the 7 coarse groups (`RiskGroup` nodes) |
| `themes.csv` | the 32 fine themes (`RiskTheme` nodes): `group_id`, the definition with its boundary rules (what the classifier is given), and the EY 2016 category it corresponds to. A change here changes every step 4c request (new replies needed) and `taxonomy_version` |
| `risk_category_map.csv` | an Item 1A level-1 heading text -> groups. `status`: `group` (names the groups in `group_ids`), `broad` (a real category naming no area: no agrees / overrules comparison), `not_a_category` (an index table, a repeated title, the summary heading). Reviewed by hand; a new text shows up as `unmapped` in step 7a |
| `reference_labels.csv` | one row per distinct lead text in Item 1A of the 313 filings (v14), with a draft primary theme read from the title only (`source: draft_title_only`). Not a gold set and never shown to the model: step 7a's `checks/risk_factors/risk_factor_themes.csv` marks the risk factors whose primary theme differs (`review: differs_from_reference`). `risk_cluster` groups rewordings of the same risk across years. |

## Design (26 Nov 2026)

- Each risk factor (a lead and the units under it) is classified once: one primary theme, and up
  to two secondary themes, each with the sentences that support it.
- Every Claim of the risk factor gets the primary theme (`scope: risk_factor`). A secondary theme
  goes only to its supporting sentences (`scope: sentence`).
- The model picks fine themes only; the group follows from `themes.csv`.
- The filing's own level-1 heading stays a Claim property, mapped to a group for the
  agrees / overrules comparison with the primary theme.

## Sources and deliberate differences

Starting point: EY / IRRCi, *The Corporate Risk Factor Disclosure Landscape* (2016), 17 categories,
fitted to our risk titles. Differences from EY, on purpose:

- AI and ESG expectations have their own themes (neither is in the 2016 study).
- Tax and trade policy are split out of EY's government and regulation.
- Climate change is its own theme (EY: natural and human disruptions), kept small on purpose
  (secondary themes will add to it).
- Resellers and distributors are customers, not supply chain (EY: supply chain and third party).
