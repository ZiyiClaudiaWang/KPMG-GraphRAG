"""The OpenAI client every paid step shares (5b risk themes, 2b page breaks, 7 label).

Money rules (Claudia's, from her checked_pilot.py):
- A request whose raw_response.json exists is never sent again (cached()).
- No automatic resend: the client does not retry, and if attempt.json exists without a
  raw_response.json (a crash or timeout after sending), the run stops for a person to check.
  One exception: a rate-limit reply (HTTP 429, not "insufficient_quota") is refused before
  any generation and is not billed, so it is sent again after a wait, at most
  rate_limit_retries times.
- The run stops before a request could take total estimated spend past budget_usd. Requests
  in flight count at their worst-case cost until their reply is in.

Each step keeps its own ledger (usage_path) and its own folder of saved requests (kind).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..common import Paths, append_jsonl, data_dir, load_env, read_jsonl

class StopRun(Exception):
    """Stop the run: budget reached, an uncertain earlier attempt, or a rejected reply."""


def canonical(value) -> str:
    """Compact, key-sorted JSON: the same value always gives the same text and hash."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)



# ---------- cost ----------

def usage_record(raw: dict, request_hash: str, latency_ms, request_id, batch: dict, cfg: dict) -> dict:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    p_in, p_cached, p_out = (cfg.get("price_input_per_1m"), cfg.get("price_cached_input_per_1m"),
                             cfg.get("price_output_per_1m"))
    estimate = None
    if None not in (p_in, p_cached, p_out) and all(type(v) is int for v in (inp, out, cached)) and 0 <= cached <= inp:
        estimate = ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_id": batch["batch_id"], "request_hash": request_hash,
        "response_id": raw.get("id"), "api_request_id": request_id, "model_returned": raw.get("model"),
        "usage_raw": usage, "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"), "generation_latency_ms": latency_ms,
        "estimated_cost_usd": estimate,
        "cost_status": "estimated_from_configured_rates" if estimate is not None else "unknown_price_or_usage",
    }


def reserve_cost(request: dict, cfg: dict) -> float:
    """Upper estimate of one request: its bytes as input tokens plus the full output allowance."""
    if cfg.get("price_input_per_1m") is None or cfg.get("price_output_per_1m") is None:
        return 0.0
    return ((len(canonical(request).encode("utf-8")) + 2048) * cfg["price_input_per_1m"]
            + request["max_completion_tokens"] * cfg["price_output_per_1m"]) / 1_000_000


# ---------- one request ----------

def no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON object key: " + key)
        out[key] = value
    return out


def parse_reply(raw: dict, schema: dict, envelope: str, duplicate_keys_ok: bool = True) -> tuple[list[str], dict]:
    """Hard problems with a raw reply (these reject it whole), and the parsed JSON when there are none."""
    from jsonschema import Draft202012Validator
    choices = raw.get("choices") or []
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        return ["Missing or incomplete completion; raw response retained."], {}
    msg = choices[0].get("message") or {}
    if msg.get("refusal"):
        return ["Model refusal; raw response retained."], {}
    try:
        hook = None if duplicate_keys_ok else no_duplicate_keys
        parsed = json.loads(msg.get("content") or "", object_pairs_hook=hook)
        if not isinstance(parsed, dict) or set(parsed) != {envelope}:
            raise ValueError("Unexpected response envelope.")
    except (ValueError, TypeError) as exc:
        return ["Invalid JSON response: " + type(exc).__name__], {}
    problems = ["Schema mismatch: " + e.message[:200] for e in Draft202012Validator(schema).iter_errors(parsed)]
    return problems, parsed



def spent_so_far(path: Path | None = None) -> float:
    path = path or Paths.usage()
    if not path.exists():
        return 0.0
    return sum(r.get("estimated_cost_usd") or 0.0 for r in read_jsonl(path))



# ---------- the client ----------

class Client:
    """Sends one request once and keeps its reply; see the module docstring for the money rules."""

    def __init__(self, cfg: dict, usage_path: Path | None = None):
        self.cfg = cfg
        self.usage_path = usage_path or Paths.usage()   # this step's ledger; budget_usd counts only it
        self.client = None              # created on the first real request
        self.spent = spent_so_far(self.usage_path)
        self.reserved = 0.0             # worst-case cost of requests in flight
        self.rate_limited = 0           # 429 replies waited out
        self.lock = threading.Lock()    # spent, reserved, client and the usage ledger

    def folder(self, request_hash: str, kind: str = "checked_pilot") -> Path:
        return data_dir() / kind / request_hash[:24]

    def cached(self, plan: dict, kind: str = "checked_pilot") -> dict | None:
        path = self.folder(plan["request_hash"], kind) / "raw_response.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def send(self, plan: dict, kind: str = "checked_pilot") -> dict:
        """Send one request once. Its raw reply is saved before anything else happens."""
        import openai  # imported here so the other steps and the tests run without the package
        folder, request_hash = self.folder(plan["request_hash"], kind), plan["request_hash"]
        attempt = folder / "attempt.json"
        if attempt.exists() and json.loads(attempt.read_text(encoding="utf-8")).get("status") != "rate_limited":
            raise StopRun(f"{plan['batch']['batch_id']}: an earlier attempt has no saved reply and may have "
                          f"been charged. Check {attempt} before sending it again.")
        budget, cost = self.cfg.get("budget_usd"), reserve_cost(plan["request"], self.cfg)
        with self.lock:
            if budget is not None and self.spent + self.reserved + cost > budget:
                raise StopRun(f"budget of ${budget} reached (spent about ${self.spent:.4f}); cached work is kept")
            self.reserved += cost
            if self.client is None:
                load_env()
                self.client = openai.OpenAI(base_url=self.cfg["base_url"],
                                            default_headers={"Accept-Encoding": "identity"})
        try:
            return self._send(plan, folder, request_hash, openai)
        finally:
            with self.lock:
                self.reserved -= cost

    def _send(self, plan: dict, folder: Path, request_hash: str, openai) -> dict:
        save_json(folder / "request.json", plan["identity"])
        rate_errors = tuple(getattr(openai, n) for n in ("RateLimitError",) if hasattr(openai, n))
        retries = int(self.cfg.get("rate_limit_retries", 6))
        for wait_round in range(retries + 1):
            save_json(folder / "attempt.json", {"request_hash": request_hash, "status": "started",
                                                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                                                "automatic_retries": wait_round, "generation_requests": 1})
            started = time.perf_counter()
            try:
                response = self.client.with_options(max_retries=0, timeout=120.0).chat.completions.create(
                    **plan["request"])
                break
            except rate_errors as exc:
                quota = "insufficient_quota" in f"{getattr(exc, 'code', '')} {exc}"
                if quota or wait_round == retries:
                    save_json(folder / "attempt.json", {"request_hash": request_hash, "status": "rate_limited",
                                                        "error_type": type(exc).__name__, "insufficient_quota": quota,
                                                        "automatic_retries": wait_round, "billing": "not billed (429)"})
                    raise StopRun(f"{plan['batch']['batch_id']}: "
                                  + ("insufficient quota" if quota else f"rate limited {retries + 1} times")
                                  + "; not billed, sent again by the next run") from exc
                (folder / "attempt.json").unlink(missing_ok=True)      # refused before generation: not billed
                with self.lock:
                    self.rate_limited += 1
                headers = getattr(getattr(exc, "response", None), "headers", None) or {}
                try:
                    pause = float(headers.get("retry-after"))
                except (TypeError, ValueError):
                    pause = min(60.0, 2.0 * 2 ** wait_round)
                time.sleep(pause)
            except Exception as exc:
                save_json(folder / "attempt.json", {"request_hash": request_hash, "status": "error_no_response",
                                                    "error_type": type(exc).__name__, "automatic_retries": 0,
                                                    "billing": "unknown", "generation_requests": 1})
                raise StopRun(f"{plan['batch']['batch_id']}: {type(exc).__name__}; not resent automatically") from exc
        record = {"request_hash": request_hash, "raw_response": response.model_dump(mode="json"),
                  "api_request_id": getattr(response, "_request_id", None),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
        save_json(folder / "raw_response.json", record)          # saved before any parsing
        usage = usage_record(record["raw_response"], request_hash, record["latency_ms"],
                             record["api_request_id"], plan["batch"], self.cfg)
        save_json(folder / "usage.json", usage)
        with self.lock:
            append_jsonl(self.usage_path, usage)
            self.spent += usage["estimated_cost_usd"] or 0.0
        return record
