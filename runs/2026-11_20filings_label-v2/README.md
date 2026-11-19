# Run 2026-11_20filings_label-v2

How to use this folder: [`runs/README.md`](../README.md).

|Run Detail|Value|
|---|---|
| Run by | wenyu-kelly-zhou |
| Run date | 2026-11-05 |
| Pipeline code | commit [54101c4] of `extraction_pipeline/` |
| Filings | 20: AAPL, CASY, MCK, MDT, MSFT, MU, NKE, NTAP, PH, TTWO, one 10-K and one 10-Q each (`extraction_pipeline/config/filings.yaml` at that commit) |
| Model | gpt-4.1-mini |
| Prompt | `label_v2` (`extraction_pipeline/prompts/label_v2.txt`) |
| LLM cost | [$1.8065, from `python -m src.label --report`] |
| Sentences | 15053 (`output/sentences.jsonl`) |
| Claims | 10774 (`output/claims.jsonl`) |
| Held for review | 1410 (`output/review_queue.jsonl`) |
