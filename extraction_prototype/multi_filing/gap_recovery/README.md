# Gap review checkpoint and candidate preparation

The selected three-filing excerpts now yield 87 source-aligned candidate Claims:
28 AAPL, 40 MSFT and 19 JPM. These include the previous 39 quality-revision
Claims and 48 additional gap-review candidates. No candidate identities overlap.
All classifications and semantic completeness remain `needs_review`.

CW's Colab run at `2026-10-17T04:12:49Z` records successful loading of this candidate
version: 96 nodes, 93 relationships and 87 verified paths. Repeated loading and
two simulated chunk-metadata changes preserved counts and relationship IDs;
original metadata was restored. The scope is `cw-extraction-three-filings-gap-v1`.
These are technical checks, not classification approval. The offline scripts in
this package do not contact Neo4j or independently rerun those live checks.

## Evidence and decisions

- All 39 target gaps have exactly one recorded assessment across nine caches:
  27 `extract`, three `exclude`, nine `needs_review`. These are recorded proposal
  decisions, not human sign-off, and are preserved without rewriting history.
- The source validator deferred eight model drafts. Their original quotes and
  fragments remain in the cache and `results/deferred_drafts.json`.
- Of 52 drafts retained by that validator, four are incomplete list boundaries
  and are also withheld. One was already in a `needs_review` assessment; three
  came from `extract` assessments. Passing source alignment alone was insufficient.
- Five complete JPM source groups (26-34, 35-40, 43-46, 49-56, 57-63) are retained
  as evidence for a pending Claim-boundary decision. They are not Claim nodes.
  The last group explicitly extends past the original excerpt ending at block 57.
- The 26 gap-stage mention proposals are retained separately with resolution
  `not_run`; they have not become canonical entities.

## Files

- `checkpoint/`: the uploaded JSON/CSV inputs and raw/processed API caches,
  including the previous quality revision. They are not overwritten by preparation.
- `gap_review_fix.py`: source validation and deferred-draft handling used in Colab.
- `prepare_gap_candidates.py`: offline replay, evidence validation, deterministic
  identity checks, boundary screening and candidate consolidation.
- `results/candidate_claims_by_ticker.json`: 87 candidates in the existing loader's
  record format. Classification labels are preserved, not upgraded to reviewed.
- `results/review_queue.csv`: candidate text, context, flags and extraction origin.
- `results/gap_assessments.json`: all original gap assessment decisions.
- `results/deferred_drafts.json`: eight source-invalid and four boundary-held drafts.
- `results/boundary_review_groups.json`: five complete list evidence groups.
- `results/mention_candidates.json`: unresolved gap-stage mention proposals.
- `results/summary.json`: counts, source scope and verification limits.
  Its `database_updated: false` describes the offline preparation operation;
  subsequent live graph results are recorded separately in `verification.json`.
- `results/verified_paths.json`, `results/verification.json`, `results/runtime.json`:
  the exact uploaded Colab path output, live-test summary and runtime versions.
- `validate_saved_graph.py`: replays preparation and validates all 87 saved paths,
  93 distinct relationship identities, provenance, evidence and verification scope.
- `Extraction_Prototype_v1_checked.ipynb`: a cleaned copy of the uploaded notebook.
  Its old malformed gap function and temporary patch cells are replaced by the
  tested helper before the batch loop. All outputs are cleared. The earlier
  pipeline still performs authenticated SEC/API/database operations when run;
  it was syntax-checked here, not rerun end to end. Appended cells prepare the
  candidate version, load it twice, verify paths and simulate chunk changes.
  Do not rerun the entire notebook just to inspect this package. A fresh run can
  incur model costs and produce different drafts; inspect the saved run offline.

## Offline replay

Run from this directory, using standard-library Python only:

```sh
python prepare_gap_candidates.py checkpoint --output results
python validate_saved_graph.py
```

Preparation verifies cached request hashes, supplied input and gap spans, exact
source-validator replay, baseline and new Claim identities, XPath/span evidence,
context evidence, provenance fields and one assessment per target. Repeated
preparation produces identical outputs. Invalid request hashes, input text,
baseline spans, saved processed quotes, gap text and classifications were each
confirmed to fail validation.

The checkpoint lacks raw SEC HTML. Its recorded source SHA-256 values are
cross-checked across records, not independently recomputed from downloaded HTML.
Offsets refer to normalized source blocks. The 80-character gap heuristic does
not measure sentence recall. Classification, context sufficiency and whole-section
completeness still require review. The saved graph tests validate this candidate
version's recorded technical run; they do not demonstrate invariance under the
Data team's actual parser/rechunking.

Repository destination: `extraction_prototype/multi_filing/gap_recovery/`.
No API credentials or Neo4j connection secrets are included.
