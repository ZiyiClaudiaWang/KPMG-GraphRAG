"""Shared paths, config loading and file helpers."""

from __future__ import annotations

import csv
import json
import os
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[1]  # the extraction_pipeline/ folder
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"


# ---------- source text ----------

# Characters the pipeline reads as a plain space (newlines, tabs, non-breaking and thin spaces).
# Each is one character, so positions in the cleaned text and in the source stay aligned.
ONE_FOR_ONE = str.maketrans({"\n": " ", "\r": " ", "\t": " ", "\u00a0": " ", "\u2009": " ", "\u202f": " "})


def source_text(content: str, spans: list[list[int]]) -> str:
    """A unit's text rebuilt from the Data team content: its pieces, whitespace read as spaces,
    joined by one space. Steps 5 and 7b compare a unit with its source through this."""
    return " ".join(content[a:b].translate(ONE_FOR_ONE) for a, b in spans)


# ---------- unit identity (#45) ----------

# Fixed namespace so the same position always yields the same id (the old claim_id namespace).
UNIT_NAMESPACE = uuid.UUID("5f0c7e2a-6b1d-4c8e-9a3f-2d7b1e4c9a60")
UNIT_IDENTITY_VERSION = "source_span_position_v1"


def unit_id(accession: str, section_key: str, spans: list[list[int]]) -> str:
    """A unit's id: uuid5 of its filing, section and source spans, never its text.

    Cleaning changes that keep a unit's spans (whitespace read as spaces, any change to the
    text alone) keep its id. A change that moves where a unit starts or ends, or which
    fragments it is made of, gives a new id (README, Unit ID & Claim ID). The same sentence at two
    places in a section is two units with two ids. A Claim's claim_id is its unit's id.
    """
    key = json.dumps([[int(a), int(b)] for a, b in spans], separators=(",", ":"))
    return str(uuid.uuid5(UNIT_NAMESPACE, f"{accession}|{section_key}|{key}"))


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
    risk_categories = staticmethod(lambda: data_dir() / "output" / "risk_categories.csv")  # step 7a
    risk_assignments = staticmethod(lambda: data_dir() / "interim" / "risk_assignments.jsonl")  # step 7a
    claims = staticmethod(lambda: data_dir() / "output" / "claims.jsonl")                  # step 7b
    claim_id_report = staticmethod(lambda: data_dir() / "output" / "claim_id_report.json")  # step 7b
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
