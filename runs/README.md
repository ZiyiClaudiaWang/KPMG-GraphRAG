# Extraction runs

Frozen results of agreed extraction pipeline runs (`extraction_pipeline/`), one folder per run.
Use them to load the graph without paying for the LLM, or to reuse the saved LLM replies when
you run the pipeline again.

`data/` at the repository root is everyone's own working folder: it is git-ignored and the
pipeline overwrites it on every run. Folders here are never edited after they are committed. A
new run gets a new folder.

## Runs

| Folder | Filings | Model | Prompt | Notes |
|---|---|---|---|---|
| [`2026-11_20filings_label-v2`](2026-11_20filings_label-v2/) | 20 (10 companies, one 10-K and one 10-Q each) | gpt-4.1-mini | `label_v2` | First full run over the project scope |

Folder names are `<YYYY-MM>_<scope>_<prompt version>`. The prompt version is
`label.prompt_version` in `extraction_pipeline/config/settings.yaml` at the time of the run.

## What is in a run folder

A run folder has the same layout as `data/`, so the pipeline can read it directly.

| Path | What it is | Pipeline step |
|---|---|---|
| `README.md` | Run details: date, code commit, model, prompt, cost | |
| `sections/` | Input: one file per filing, exported from the Data team database | 1 Fetch |
| `interim/clean/` | Cleaned sections (also read by step 8 Load) | 2 Clean |
| `interim/label_batches.jsonl`, `interim/risk_assignments.jsonl` | Working files of steps 5 and 7a | 5, 7a |
| `output/section_manifest.csv` | Which sections were kept | 3 Filter |
| `output/sentences.jsonl` | Every sentence and list group, with its `unit_id` | 4 Segment |
| `output/labels.jsonl` | One LLM label per sentence | 5 Label |
| `output/usage.jsonl` | Tokens and cost of every API call | 5 Label |
| `output/errors.jsonl`, `output/review_queue.jsonl` | Label checks and units held for review | 6 Validate |
| `output/risk_categories.csv` | Risk headings and their mapped themes | 7a Risk factors |
| `output/claims.jsonl` | **The Claims: one row per claim, with entity mentions and source spans** | 7b Build Claims |
| `output/claim_id_report.json` | Claim ids added, removed or changed against the previous build | 7b |
| `checked_pilot/`, `targeted_repair/` | Every LLM request and its raw reply, one folder per request | 5 Label |

Field definitions: `extraction_pipeline/README.md`, Appendix A.

## Use 1: read the results

Most people need only two files:

- `output/claims.jsonl`: one JSON object per line, one line per Claim
- `output/sentences.jsonl`: the sentences the Claims come from, joined on `unit_id`

They are plain JSON Lines. In Python: `pandas.read_json(path, lines=True)`.

## Use 2: load a run into Neo4j

No API key needed. Set up `extraction_pipeline/` first (README there, "Setup"), with your Neo4j
details in `extraction_pipeline/.env`. Then, from `extraction_pipeline/`:

```bash
PIPELINE_DATA_DIR=../runs/2026-11_20filings_label-v2 python run_pipeline.py --steps load --dry-run
PIPELINE_DATA_DIR=../runs/2026-11_20filings_label-v2 python run_pipeline.py --steps load
```

`PIPELINE_DATA_DIR` makes the pipeline read this folder instead of `data/`.

- The dry run connects to nothing and writes `output/load_preview.cypher` into the run folder.
  That file is git-ignored; do not commit it.
- Every node and edge is stamped with `load.scope` from `settings.yaml`. To remove what you
  loaded: `PIPELINE_DATA_DIR=../runs/<run> python -m src.load --wipe`.
- Only run `--steps load` against a run folder. Any other step would overwrite the run's files.

## Use 3: run the pipeline again, reusing the saved LLM replies

Work in `data/`, never in a run folder. From the repository root:

```bash
mkdir -p data
cp -R runs/2026-11_20filings_label-v2/sections data/
cp -R runs/2026-11_20filings_label-v2/checked_pilot data/
cp -R runs/2026-11_20filings_label-v2/targeted_repair data/
```

Then run the pipeline as usual (`extraction_pipeline/README.md`, "Run").

- Step 5 looks up each request by its hash. A request whose model, prompt, sentences, context
  and `section_path` are all unchanged reads the saved reply and costs nothing. Anything else is
  sent to OpenAI. Details: `extraction_pipeline/README.md`, "Cost tracking".
- Do not copy `output/usage.jsonl`. `budget_usd` counts the spend recorded there, so the old
  run's cost would count against your new run and stop it early.
- Copying into a `data/` that already has replies is safe: each request has its own folder.

## Adding a new run

1. Run the pipeline in `data/` and check the results (`extraction_pipeline/README.md`,
   "What to review").
2. Copy `data/` to `runs/<YYYY-MM>_<scope>_<prompt version>/`, without
   `output/load_preview.cypher` and `.DS_Store` files.
3. Write the run's `README.md` (copy an existing one) and add a row to the table above.
4. Commit on a branch and open a pull request. Do not edit earlier run folders.

Check the size first: GitHub refuses any file over 100 MB.
