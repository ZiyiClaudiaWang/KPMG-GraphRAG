# Maintaining the extraction pipeline

This guide describes the pipeline at commit `3ca2aab`. It complements the [pipeline README](README.md), which contains the field definitions, examples, and remaining design decisions.

## Stage 1. Turn exported filing sections into source-linked units

Start in `extraction_pipeline/` with the dependencies from `requirements.txt` installed. The pipeline expects one JSONL export per filing under `data/sections/`. The default data directory is `../data` relative to this directory. Set `PIPELINE_DATA_DIR` to keep a trial separate from an existing run, and `PIPELINE_FILINGS` to point to a separate filing manifest if needed. See the [Data-team export instructions](../docs/data-access.md) for database setup.

```bash
python run_pipeline.py --steps fetch
python run_pipeline.py --steps clean,filter,segment
python run_pipeline.py --steps label --dry-run
```

- `fetch` checks exports against `config/filings.yaml`. Add or remove filings there for a planned corpus change.
- `clean` removes page furniture and repairs paragraphs. `filter` selects sections according to `config/sections.yaml` and records those decisions in `output/section_manifest.csv`.
- `segment` writes `output/sentences.jsonl`. Each unit carries its filing, section key and raw source spans. Review headings, bullets and page-break examples when changing `src/clean.py`, `src/segment.py` or the corresponding settings.
- The label dry run reports the planned work and estimated worst-case cost without sending new requests. It writes local planning and label outputs, so run it in a separate data directory when preserving an existing run matters.

## Stage 2. Label units, validate them and build Claims

`src/label.py` uses the prompt named by `label.prompt_version` in `config/settings.yaml`. The current default is [label_v2.txt](prompts/label_v2.txt); [repair_v1.txt](prompts/repair_v1.txt) handles targeted repair. Label definitions live in `config/claim_types.yaml`. Before changing a prompt, check `response_schema()` and how `src/validate.py` and `src/build_claims.py` consume the resulting fields. Record the prompt version and model with the run.

With the run budget and filing scope agreed, run `python run_pipeline.py --steps label` with an OpenAI key configured through `.env`. The model labels eligible units and can refer to supplied context. Rule-filtered units get local labels. Request and reply files under `checked_pilot/` and `targeted_repair/` help diagnose failures. Usage is logged in `output/usage.jsonl`.

```bash
python -m src.validate
python run_pipeline.py --steps risk_factors,build_claims
python run_pipeline.py --steps load --dry-run
```

Run these in order and stop if validation exits with an error. The standalone validator returns a failing exit status for label errors. It also writes a review queue. The risk step maps recognized categories to themes using `config/risk_theme_map.csv` and `config/risk_themes.csv`. The builder writes `output/claims.jsonl` and leaves held units out. The separate Exhibit 21 path produces `output/subsidiaries.jsonl`.

A Claim is one statement at one source position. Its `claim_id` is the `unit_id`, derived from accession, section key and raw spans. Compare `output/claim_id_report.json` between builds before relying on older Claim references. Repeated wording can belong to several source positions or filings, each with its own citation.

## Where to look when something breaks

| Symptom | First check |
|---|---|
| Filing missing or processing version unexpected | Check the export and manifest. Compare the accession and `processing_set_id` before rerunning downstream steps. |
| A sentence is cut, missing or classified as a heading | Compare the raw section, `interim/clean/` and `output/sentences.jsonl`. Fix the first stage that changes it incorrectly. |
| Label request stopped or response rejected | Inspect the request folder and its validation errors. If an attempt exists without a saved response, check usage before retrying. |
| Claim missing | Check `output/errors.jsonl`, then `output/review_queue.jsonl` and the unit's label. |
| Risk-theme link missing | Check `output/risk_categories.csv` for unmapped or absent categories and inspect the theme map. |
| Counts grew after rebuilding | Compare the Claim ID report and confirm the graph scope. The loader merges current IDs and leaves old IDs in place if they changed. Use a fresh agreed scope to compare builds. |

## Graph v2 acceptance

1. Review `output/load_preview.cypher`, the filing manifest and the Claim ID report. Record the code revision, export versions, prompt version and target `pipeline_scope`. Configure the agreed Neo4j instance through `.env`.
2. Load the same artifacts twice into that scope using `python run_pipeline.py --steps load`. After each load, run the corpus report from the repo root and save the observed counts. Use `--instance` and `--scope` to select the intended graph. See [Neo4j instances](../docs/neo4j-instances.md).
3. Compare the two reports. Verify the expected filing count, stable node and edge counts, no orphan Claims, and one `SUPPORTS` link per Claim. In the re-chunk check, keep source positions fixed and verify that changing chunk metadata does not create a second edge. A changed segmentation boundary can legitimately change Claim identity and should be reviewed separately.
4. Run `python -m pytest -q tests/test_toy_graph_fixture.py tests/test_corpus_report.py` from the repo root with the development dependencies installed. These tests check the fixture and reporting logic. Save the two live reports as evidence of the actual load.

See the [sprint acceptance criteria](../docs/sprints/2026-11-16-acceptance.md#extraction-1--claudia) for sign-off.

### Repeated sentences and label consistency

In the 20-filing pilot, 127 of 1,579 repeat groups (8.0%) had conflicting primary labels when grouped by company, section type and exact text. After also matching form, section path and pre-batch context, nine groups still showed differences in subject scope or risk-heading flags. The denominator is groups. These checks do not establish a per-sentence error rate. See [the pilot report](https://github.com/ZiyiClaudiaWang/KPMG-GraphRAG/pull/88).

Link repeated disclosures across filings while retaining each occurrence’s Claim ID, accession and source span for Eval. Treat a near match as a review candidate. A changed number, date, negation or modal verb may change the meaning. Run the existing exact-repeat report when a new claims file arrives.
