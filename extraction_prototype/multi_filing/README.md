# Three-filing extraction prototype

Latest: [gap-review candidate graph](gap_recovery/README.md) records 87 candidate
Claims, 96 nodes and 93 relationships. CW's saved Colab run verifies all 87 source
paths, repeated loading and two simulated chunk-metadata changes with restoration.
All classifications remain `needs_review`; full-section completeness is unestablished.
The [39-Claim source revision](quality_revision/README.md) and original 38-Claim
run below remain available as historical records.

Selected Item 1A passages from Apple, Microsoft and JPMorgan Chase. CW ran this
workflow in Colab against a Neo4j Sandbox. The saved record contains 38 Claims,
47 nodes and 44 relationships. This is a technical prototype; all Claims remain
`needs_review`, and extraction completeness has known gaps.

| Filing | Accession | Claims | Issuer mentions resolved | Cross-block Claims |
| --- | --- | ---: | ---: | ---: |
| AAPL FY2025 | 0000320193-25-000079 | 9 | 1 | 0 |
| MSFT FY2025 | 0000950170-25-100235 | 15 | 0 | 0 |
| JPM FY2025 | 0001628280-26-008131 | 14 | 3 | 1 |

## Inspect the saved run

From the repository root, with Python 3:

```bash
python extraction_prototype/multi_filing/validate_saved_results.py
```

The script uses only the standard library. It checks request fingerprints, source
metadata, extracted text and spans, ordered cross-block joins, deterministic IDs,
mention resolution, the review CSV and all 38 saved paths. It does not contact
Claude, SEC or Neo4j, rerun live tests, or establish semantic correctness.

`results/verification.json` records the user's successful live repeat-loading and
simulated chunk-metadata checks. `results/verified_paths.json` holds the returned
paths. Actual Data-team re-chunking was not tested. Original chunk metadata was
restored after the simulations.

## Reproduce in Colab

Open `Three_Filing_Prototype.ipynb` and run cells in order. It preserves the initial
Apple single-paragraph pilot, then prepares three filings and runs the larger
excerpt workflow. A fresh full run can make four Claude requests and incur API
costs. Supply a SEC contact email, an Anthropic API key and Neo4j credentials at
runtime. Notebook outputs are cleared. API credentials are not in the saved data.

The notebook downloads the working `claudia/ontology-merge` branch. SEC HTML is
stored in `/content/`; checksums are recorded in each filing's metadata. Runtime
package versions are recorded, not enforced by a lockfile. The selected model
must be available on the supplied API account. Local cache files avoid identical
repeat requests; a fresh runtime may return different model outputs and counts.

Section boundaries were inspected manually for these three exact source documents.
Microsoft has repeated item page labels. JPMorgan splits sentences across HTML
blocks: Claim evidence therefore supports multiple ordered XPath/span fragments.
Do not assume this parser configuration generalizes to another report unchanged.

The graph scope is `cw-extraction-three-filings-v1`, separate from the initial
single-paragraph pilot and the constructed schema probe. Company identities use
filing CIKs; alias resolution is a separate, limited rule stage. The Microsoft
Company node is supported by filing metadata even though the model missed its
issuer mention. The graph has Company, Filing, Section and Claim nodes only.
Unresolved mentions and RiskFactor candidates remain in saved resolution records.

## Quality findings and limits

Read `review_findings.json` before using these Claims for answers. It records:

- An Apple sentence returned by the first pilot but omitted in the larger run.
- Missing context for JPM's `This situation` sentence.
- Missing Microsoft issuer mentions.
- An issuer-defined legal term requiring scope review.
- Incomplete list context for JPM's `These types of changes` sentence.

Original extraction and live verification records are unchanged. These findings
are open review items, not post-hoc corrections asserted to have been loaded.
`results/review_queue.csv` contains all 38 classifications for review. Source
matching does not measure recall or independently verify financial assertions.

This is a roughly 6,000-character initial passage per company, not complete Item
1A extraction or a production end-to-end pipeline. Sections use provisional local
keys pending Data alignment. The live path query is not integrated with the team
API. The latest candidate version reaches a roughly 100-node technical graph;
the broader Extraction v1 milestone remains incomplete.
Claim identity groups equal normalized sentences within a section; production
occurrence identity and cross-parser stability require further decisions.

Full HTML is not bundled. Offline XPath checks compare the saved evidence with
saved source excerpts, rather than verifying the XPath against the original DOM.
