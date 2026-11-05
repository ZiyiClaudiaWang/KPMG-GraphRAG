# Exhibit 21 Parent–Subsidiary Pipeline

Turns the **Exhibit 21** ("Subsidiaries of the Registrant") attached to a company's Form 10-K into
a clean parent → subsidiary table, tracks how the list changes year to year, and exports graph
edges for Graph RAG.

Add any company's filings, run one command, and you get:

| Output | Who it is for | What it contains |
|---|---|---|
| `output/parent_subsidiary_report.xlsx` | People | Every subsidiary of every company, by year, with changes and sources |
| `output/graph_edges.csv` | Graph RAG / graph databases | `Parent —HAS_SUBSIDIARY→ Subsidiary` and `Subsidiary —INCORPORATED_IN→ Country` edges |

---

## 1. Quick start (5 minutes)

```bash
git clone <this repo> && cd ex21_pipeline
pip install -r requirements.txt

python run_pipeline.py          # builds the report from the filings already in data/raw/
```

**Google Colab:** open `colab_demo.ipynb` in Colab (File → Upload notebook) and run the cells.
It walks through uploading the project zip (or cloning the repo), running the pipeline,
viewing the tables, and downloading the outputs. A `.py` file cannot be opened in Colab on its
own; it has to run inside the unzipped project folder, which the notebook sets up for you.

The repo already contains Apple and Microsoft filings, so this works immediately. Open
`output/parent_subsidiary_report.xlsx` and start at the **Parent → Subsidiaries** sheet.

## 2. Adding more companies

There are two ways to get filings into `data/raw/`. Use either one, then run
`python run_pipeline.py` again.

### Option A: automatic download (recommended)

```bash
export SEC_USER_AGENT="Your Name your.email@school.edu"   # SEC requires a contact; use your own
python fetch_edgar.py NVDA JPM KO --years 3
python run_pipeline.py
```

- **Ticker** = the stock symbol (AAPL = Apple, MSFT = Microsoft, NVDA = Nvidia). You do **not**
  download anything yourself: the script looks the ticker up on SEC EDGAR, finds the latest
  `--years` 10-K filings, and downloads each one's Exhibit 21.
- Pass **as many tickers as you like in one command**. Already-downloaded filings are skipped,
  so re-running is safe.
- Each file is recorded in `data/manifest.csv` with the company name, CIK, fiscal year,
  accession number and SEC link. These details end up in the report.
- If a 10-K has no Exhibit 21 attached, that filing is listed in `data/fetch_log.csv`.

On Windows PowerShell use `$env:SEC_USER_AGENT="Your Name your.email@school.edu"`.

### Option B: drop files in by hand

If you already have the exhibit files, copy them (as many as you want at once) into `data/raw/`
using this name pattern:

```
TICKER_FY<fiscal year>_<accession number>_ex21.htm
e.g.  NVDA_FY2025_0001045810-25-000023_ex21.htm
```

The pipeline picks them up automatically, with no need to edit `manifest.csv`. Supported file
types: `.htm`, `.html`, `.txt`, `.md`. Hand-added files show the ticker as the company name and
have no SEC link. To fill those in, add a row to `data/manifest.csv`, or use Option A.

A file whose name does not follow the pattern is not parsed. It is listed in the report's
**QA** sheet as `SKIPPED`.

## 3. What the pipeline does

```
fetch_edgar.py (optional)            run_pipeline.py
┌──────────────────────┐   ┌───────────────────────────────────────────────────────────┐
│ ticker → SEC EDGAR   │   │ parse            normalize         match across years      │
│ latest N 10-Ks       │──▶│ HTML table /  →  country + state → same entity? renamed? → │──▶ report.xlsx
│ download EX-21       │   │ text list         legal-suffix key  added / removed       │    graph_edges.csv
│ → data/raw/ + manifest│   │ indentation = sub-of-sub                                  │
└──────────────────────┘   └───────────────────────────────────────────────────────────┘
```

1. **Parse** (`ex21/parse.py`). Exhibit 21 has no required format, so the parser handles the
   common layouts: HTML tables (including blank spacer columns, `% owned` columns and
   footnote marks such as `*` or `(1)`), tables that use **indentation** to show which
   subsidiary owns which, and plain-text lists (`Name (Delaware)` or `Name ...... Japan`).
2. **Normalize** (`ex21/normalize.py`). Jurisdictions are split into country and state, so
   `Delaware, U.S.` becomes *United States / Delaware*. Each name also gets a matching key
   with legal suffixes removed (Inc., Limited, Unlimited Company, ...).
3. **Match across years** (`ex21/build.py`). Identical names are treated as the same entity.
   A name that matches once suffixes are removed is treated as a **rename**. Anything else is
   **added** or **removed**.
4. **Report** (`ex21/report.py`). Writes the Excel workbook and the graph edges.

## 4. The outputs, using Apple and Microsoft as the example

The repo ships with six filings: Apple FY2023–FY2025 and Microsoft FY2024–FY2026. Running the
pipeline on them prints:

```
                 filings: 6
         subsidiary_rows: 80
   distinct_subsidiaries: 29
  year_over_year_changes: 6
    files_needing_review: []
```

### `parent_subsidiary_report.xlsx`

| Sheet | Question it answers |
|---|---|
| **README** | What each sheet means and how to read it |
| **Summary** | How many subsidiaries and countries per company per year, and how many changed? |
| **Parent → Subsidiaries** | What is each company's latest subsidiary list? *(start here)* |
| **Timeline** | Which years was each subsidiary listed? |
| **Changes** | What was added, removed or renamed, and when? |
| **All Records** | Every parsed row, with accession number and SEC link (for checking) |
| **QA** | Did every file parse? Which files need a human look? |

**Summary** (from the example data):

| Ticker | Fiscal year | Subsidiaries listed | Countries | Added | Removed | Renamed |
|---|---|---|---|---|---|---|
| AAPL | 2023 | 18 | 12 | 0 | 0 | 0 |
| AAPL | 2024 | 18 | 12 | 0 | 0 | 0 |
| AAPL | 2025 | 19 | 13 | 2 | 1 | 0 |
| MSFT | 2024 | 9 | 2 | 0 | 0 | 0 |
| MSFT | 2025 | 8 | 2 | 0 | 1 | 2 |
| MSFT | 2026 | 8 | 2 | 0 | 0 | 0 |

**Parent → Subsidiaries** (excerpt):

| Parent company | Subsidiary | Country | State | 3-year status |
|---|---|---|---|---|
| Apple Inc. (AAPL) | Apple Operations International Limited | Ireland | | Listed every year |
| Apple Inc. (AAPL) | Braeburn Capital, Inc. | United States | Nevada | Listed every year |
| Apple Inc. (AAPL) | Apple Services Pte. Ltd. | Singapore | | New in FY2025 |
| Microsoft Corporation (MSFT) | Activision Blizzard, Inc. | United States | | Listed every year |
| Microsoft Corporation (MSFT) | Microsoft Ireland Research Unlimited Company | Ireland | | Listed every year · name changed |

**Changes**, i.e. what the example data shows:

| Parent | FY | Change | Subsidiary | Detail |
|---|---|---|---|---|
| AAPL | 2025 | Added | Apple Services Pte. Ltd. (Singapore) | First appears this year |
| AAPL | 2025 | Added | Apple South Asia Pte. Ltd. (Singapore) | First appears this year |
| AAPL | 2025 | Removed | Apple Sales International Limited (Ireland) | Not in this year's Exhibit 21 |
| MSFT | 2025 | Removed | Nuance Communications, Inc. | Not in this year's Exhibit 21 |
| MSFT | 2025 | Renamed | Microsoft Global Finance Unlimited Company | Previously "Microsoft Global Finance" |
| MSFT | 2025 | Renamed | Microsoft Ireland Research Unlimited Company | Previously "Microsoft Ireland Research" |

### `graph_edges.csv`

One row per relationship per year, ready to load into a graph store (Neo4j, NetworkX, a
Graph RAG index):

| source | target | relation | fiscal_year | target_country | evidence |
|---|---|---|---|---|---|
| Apple Inc. | Apple Asia LLC | HAS_SUBSIDIARY | 2025 | United States | Exhibit 21 (0000320193-25-000079) |
| Apple Asia LLC | United States | INCORPORATED_IN | 2025 | United States | Exhibit 21 (0000320193-25-000079) |
| Microsoft Corporation | Activision Blizzard, Inc. | HAS_SUBSIDIARY | 2026 | United States | Exhibit 21 (0001193125-26-323660) |

Every edge has `confidence = filed`, because it comes straight from the company's own filing.
If edges extracted from 10-K text are added later, give them a different confidence value so
the two sources stay distinguishable.

## 5. Using the results afterwards

- **Graph RAG**: load `graph_edges.csv` as the backbone of the company graph. Then attach other
  relationships, such as acquisitions, equity investments or litigation from 10-K text, to the
  same subsidiary nodes.
- **Validation**: compare entities you extract from 10-K text against the **Parent →
  Subsidiaries** sheet to measure extraction accuracy.
- **Monitoring**: re-run `fetch_edgar.py` after each 10-K season. The **Changes** sheet shows
  what each company added or dropped.

## 6. Reading the results correctly

- Exhibit 21 lists **significant subsidiaries only** (Reg. S-K Item 601(b)(21)). Apple lists
  about 19 and Microsoft about 8, but both have many more subsidiaries in total.
- A flat list does not say whether a subsidiary is held **directly or indirectly**, so the
  relationship reads "direct/indirect not stated". When a filing uses indentation, the
  `immediate_parent` column records the subsidiary that owns it.
- **"Removed"** only means the entity is absent from that year's list. It may have merged, been
  sold or renamed, or dropped below the significance threshold. Check the 10-K before
  concluding anything.
- **A complete rebrand** (e.g. *Facebook, Inc.* → *Meta Platforms, Inc.*) cannot be detected from
  names alone. It appears as one Removed and one Added entry.
- Exhibits filed as **images or PDFs**, or "incorporated by reference" to an earlier filing,
  produce 0 rows and are flagged on the **QA** sheet.

## 7. Repository layout

```
ex21_pipeline/
├── fetch_edgar.py        Step 1 (optional): download Exhibit 21s from SEC EDGAR
├── run_pipeline.py       Step 2: build the report
├── colab_demo.ipynb      run everything in Google Colab
├── requirements.txt
├── data/
│   ├── manifest.csv      list of filings + metadata (updated by fetch_edgar.py)
│   └── raw/              one Exhibit 21 file per company per year
├── ex21/                 pipeline code (parse → normalize → build → report)
├── output/               parent_subsidiary_report.xlsx, graph_edges.csv
└── tests/test_parse.py   format tests:  python tests/test_parse.py
```

**About the bundled Apple and Microsoft files:** they are `.md` text renderings of the SEC HTML
exhibits. Each file's first line gives its original SEC link. Files downloaded with
`fetch_edgar.py` are the original HTML. The parser handles both formats.

**Status:** `run_pipeline.py` and the parser are tested on the six bundled filings and on the
format tests. `fetch_edgar.py` follows SEC EDGAR's public endpoints but was built without live
network access. On its first real run, check `data/fetch_log.csv` and the **QA** sheet.
