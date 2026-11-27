"""Model settings and cost estimates for the small LLM steps (now only 2b page breaks).

Each step has its own block in config/settings.yaml (page_breaks:) with its own
model, reasoning effort and budget_usd, and its own ledger (data/output/usage_<step>.jsonl), so it
never spends step 7's budget and step 7 never spends its budget. Token prices come from the
shared `prices:` table, so changing a step's model needs no other edit.

Step 7 (label.py) is not changed by this module: its request, and so its request hashes, are
built from the label: block as before.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..common import Paths, read_jsonl, settings

# Models that think before answering. They take reasoning_effort, not temperature, and their
# reasoning tokens are billed as output and count against max_completion_tokens.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Rough reasoning tokens per request, for the "typical" estimate only (the budget uses the
# worst case). Replace with measured numbers: after a --limit run the dry run uses the ledger.
REASONING_GUESS = {"none": 0, "minimal": 50, "low": 400, "medium": 1500, "high": 4000}


def is_reasoning(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


def step_cfg(step: str, model: str | None = None) -> dict:
    """The settings of a small LLM step, with prices filled in from the prices: table.

    base_url falls back to label.base_url. model overrides the configured one (--model).
    """
    all_settings = settings()
    cfg = dict(all_settings.get(step) or {})
    if model:
        cfg["model"] = model
    if "model" not in cfg:
        raise SystemExit(f"config/settings.yaml has no {step}.model")
    cfg.setdefault("base_url", all_settings["label"]["base_url"])
    cfg.update(prices_for(cfg["model"], cfg["base_url"], all_settings))
    return cfg


def prices_for(model: str, base_url: str, all_settings: dict | None = None) -> dict:
    """price_*_per_1m keys for a model (None when it is not in the table: no budget check)."""
    all_settings = all_settings or settings()
    row = (all_settings.get("prices") or {}).get(model)
    if row is None:
        return {"price_input_per_1m": None, "price_cached_input_per_1m": None, "price_output_per_1m": None}
    host = urlparse(base_url).hostname or ""
    regional = host != "api.openai.com" and host.endswith(".api.openai.com")
    factor = 1 + (all_settings.get("regional_uplift") or 0) if (regional and row.get("regional_uplift")) else 1
    return {"price_input_per_1m": row["input"] * factor,
            "price_cached_input_per_1m": row.get("cached_input", row["input"]) * factor,
            "price_output_per_1m": row["output"] * factor}


def model_params(request: dict, cfg: dict, answer_tokens: int) -> dict:
    """Add the model-specific fields to a request: temperature for gpt-4.1/gpt-4o, reasoning
    effort and room for reasoning tokens for reasoning models."""
    model = cfg["model"]
    request["max_completion_tokens"] = answer_tokens
    if model.startswith(("gpt-4.1", "gpt-4o")):
        request["temperature"] = 0
    elif is_reasoning(model):
        effort = cfg.get("reasoning_effort")
        if effort:
            request["reasoning_effort"] = effort
        request["max_completion_tokens"] = answer_tokens + int(cfg.get("max_reasoning_tokens", 0))
    return request


def estimate(plans: list[dict], cfg: dict, answer_tokens_per_request: list[int]) -> dict:
    """Costs of a list of plans: worst case (what budget_usd checks) and typical.

    typical: input = request bytes / 3.5 (JSON, mostly English), output = the expected answer plus
    REASONING_GUESS for the effort. Both are rough; measure with --limit before a large run.
    """
    from .client import canonical, reserve_cost
    p_in, p_out = cfg.get("price_input_per_1m"), cfg.get("price_output_per_1m")
    if p_in is None or p_out is None:
        return {"worst": None, "typical": None, "input_tokens": None}
    reasoning = REASONING_GUESS.get(cfg.get("reasoning_effort") or "none", 0) if is_reasoning(cfg["model"]) else 0
    in_tokens = sum(len(canonical(p["request"]).encode("utf-8")) / 3.5 for p in plans)
    out_tokens = sum(answer_tokens_per_request) + reasoning * len(plans)
    return {"worst": sum(reserve_cost(p["request"], cfg) for p in plans),
            "typical": (in_tokens * p_in + out_tokens * p_out) / 1_000_000,
            "input_tokens": round(in_tokens), "output_tokens": round(out_tokens)}


def measured(step: str, model: str) -> dict | None:
    """Average cost and tokens per request already sent by this step with this model."""
    path = Paths.step_usage(step)
    if not path.exists():
        return None
    def same(returned: str) -> bool:     # "gpt-5.4-2026-03-05" is gpt-5.4, "gpt-5.4-mini-..." is not
        return returned == model or returned.startswith(model + "-20")
    rows = [r for r in read_jsonl(path) if same(r.get("model_returned") or "")
            and r.get("estimated_cost_usd") is not None]
    if not rows:
        return None
    n = len(rows)
    return {"n": n, "cost": sum(r["estimated_cost_usd"] for r in rows) / n,
            "output_tokens": sum(r.get("output_tokens") or 0 for r in rows) / n,
            "reasoning_tokens": sum(r.get("reasoning_tokens") or 0 for r in rows) / n}


def report(step: str, cfg: dict, build_plans, answer_tokens, log, compare: bool = False) -> None:
    """Log the cost of this step's requests; with compare, for every model in the prices table.

    build_plans(cfg) -> plans; answer_tokens(plans) -> expected answer tokens per request.
    """
    models = list(settings().get("prices") or {}) if compare else [cfg["model"]]
    if compare:
        log(step, f"{'model':<14}{'typical':>10}{'worst case':>12}   (reasoning_effort "
                  f"{cfg.get('reasoning_effort') or '-'} for reasoning models; budget ${cfg.get('budget_usd')})")
    for model in models:
        c = step_cfg(step, model) | {k: cfg[k] for k in ("reasoning_effort", "max_reasoning_tokens", "budget_usd")
                                     if k in cfg}
        plans = build_plans(c)
        e = estimate(plans, c, answer_tokens(plans))
        m = measured(step, model)
        if e["worst"] is None:
            log(step, f"{model}: no price in config/settings.yaml prices:, cost unknown")
            continue
        if compare:
            mark = " <- configured" if model == cfg["model"] else ""
            log(step, f"{model:<14}{'$%.3f' % e['typical']:>10}{'$%.3f' % e['worst']:>12}{mark}")
        else:
            log(step, f"{len(plans)} requests with {model}: typical about ${e['typical']:.3f} "
                      f"(~{e['input_tokens']:,} input, ~{e['output_tokens']:,} output tokens), "
                      f"worst case ${e['worst']:.3f}; budget ${c.get('budget_usd')}, "
                      f"spent so far ${_spent(step):.4f}")
        if m:
            log(step, f"  measured on {m['n']} sent requests: ${m['cost']:.4f} per request "
                      f"({m['output_tokens']:.0f} output tokens, {m['reasoning_tokens']:.0f} of them reasoning) "
                      f"-> about ${m['cost'] * len(plans):.3f} for all {len(plans)}")


def _spent(step: str) -> float:
    from .client import spent_so_far
    return spent_so_far(Paths.step_usage(step))
