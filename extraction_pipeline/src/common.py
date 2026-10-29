"""Shared paths, config loading and file helpers."""

from __future__ import annotations

import csv
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[1]  # the extraction_pipeline/ folder
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"


# ---------- config ----------

@lru_cache(maxsize=None)
def load_config(name: str) -> dict:
    """Load config/<name>.yaml."""
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def settings() -> dict:
    return load_config("settings")


def filings() -> list[dict]:
    """Filings from config/filings.yaml, or from the file named by PIPELINE_FILINGS (used by tests)."""
    override = os.environ.get("PIPELINE_FILINGS")
    if override:
        with open(override, encoding="utf-8") as f:
            return yaml.safe_load(f)["filings"]
    return load_config("filings")["filings"]


def filing_stem(filing: dict) -> str:
    """File name stem used by graphrag_data.py exports, e.g. AAPL_0000320193-25-000079."""
    return f"{filing['ticker']}_{filing['accession']}"


# ---------- paths ----------

def data_dir() -> Path:
    """settings.yaml data_dir, or PIPELINE_DATA_DIR when set (used by tests)."""
    return Path(os.environ.get("PIPELINE_DATA_DIR") or (ROOT / settings()["data_dir"])).resolve()


class Paths:
    """Every file the pipeline reads or writes, in one place."""

    @staticmethod
    def sections(filing: dict) -> Path:            # step 1 output (input to the pipeline)
        return data_dir() / "sections" / f"{filing_stem(filing)}.jsonl"

    @staticmethod
    def cleaned(filing: dict) -> Path:             # step 2
        return data_dir() / "interim" / "clean" / f"{filing_stem(filing)}.jsonl"

    manifest = staticmethod(lambda: data_dir() / "output" / "section_manifest.csv")        # step 3
    sentences = staticmethod(lambda: data_dir() / "output" / "sentences.jsonl")            # step 4
    batches = staticmethod(lambda: data_dir() / "interim" / "label_batches.jsonl")         # step 5
    labels = staticmethod(lambda: data_dir() / "output" / "labels.jsonl")                  # step 5
    usage = staticmethod(lambda: data_dir() / "output" / "usage.jsonl")                    # step 5
    label_cache = staticmethod(lambda: data_dir() / "cache" / "label")                     # step 5
    errors = staticmethod(lambda: data_dir() / "output" / "errors.jsonl")                  # step 6
    review_queue = staticmethod(lambda: data_dir() / "output" / "review_queue.jsonl")      # step 6
    risk_headings = staticmethod(lambda: data_dir() / "output" / "risk_headings.csv")      # step 7
    risk_factors = staticmethod(lambda: data_dir() / "output" / "risk_factors.csv")        # step 7
    risk_assignments = staticmethod(lambda: data_dir() / "interim" / "risk_assignments.jsonl")  # step 7
    claims = staticmethod(lambda: data_dir() / "output" / "claims.jsonl")                  # step 7
    cypher = staticmethod(lambda: data_dir() / "output" / "load_preview.cypher")           # step 8


# ---------- files ----------

def read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_env(path: Path | None = None) -> None:
    """Read KEY=value lines from extraction_pipeline/.env into os.environ (existing values win)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def log(step: str, message: str) -> None:
    print(f"[{step}] {message}", flush=True)
