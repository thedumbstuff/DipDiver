"""LLM proposer — single function: take context, return a validated Proposal.

OpenAI SDK with a base_url override works for DeepSeek (their API is
OpenAI-compatible), OpenAI proper, and most providers in between. No LiteLLM,
no Jinja, no YAML — one client, one call, one Pydantic validation.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

from openai import OpenAI
from pydantic import ValidationError

from dipdiver.brain.m2.lite.prompts import SYSTEM_PROMPT, render_user_prompt
from dipdiver.brain.m2.lite.schema import LoopRecord, Proposal

log = logging.getLogger(__name__)


# DeepSeek's published pricing as of late 2025. LiteLLM's registry doesn't
# know about deepseek-v4-pro so we hardcode here. Update if pricing changes.
_PROVIDER_PRICING = {
    # provider -> (input $/M, output $/M)
    "deepseek": (0.27, 1.10),
    "openai_gpt4o": (2.50, 10.00),
    "openai_o3": (15.00, 60.00),
    "anthropic_sonnet": (3.00, 15.00),
    "anthropic_opus": (15.00, 75.00),
}


@dataclass(frozen=True)
class ProposerConfig:
    model: str                    # e.g. "deepseek-v4-pro"
    base_url: str                 # e.g. "https://api.deepseek.com/v1"
    api_key_env: str              # name of env var holding the key
    pricing_tier: str             # key into _PROVIDER_PRICING
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout_seconds: int = 180


def _client(cfg: ProposerConfig) -> OpenAI:
    key = os.environ.get(cfg.api_key_env)
    if not key:
        raise RuntimeError(f"{cfg.api_key_env} not set in environment")
    return OpenAI(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout_seconds)


def _cost(input_tokens: int, output_tokens: int, tier: str) -> float:
    if tier not in _PROVIDER_PRICING:
        return 0.0
    in_price, out_price = _PROVIDER_PRICING[tier]
    return (input_tokens / 1e6) * in_price + (output_tokens / 1e6) * out_price


def propose(
    cfg: ProposerConfig,
    *,
    universe: str,
    region: str,
    train_start: str,
    test_start: str,
    test_end: str,
    benchmark: str,
    m1_sharpe: float,
    m1_ann_return: float,
    prior_loops: list[LoopRecord],
) -> tuple[Proposal, int, int, float, float]:
    """Single LLM call → Proposal. Retries once on JSON / validation error.

    Returns (proposal, in_tokens, out_tokens, cost_usd, wall_seconds).
    """
    client = _client(cfg)
    user_prompt = render_user_prompt(
        universe=universe, region=region,
        train_start=train_start, test_start=test_start, test_end=test_end,
        benchmark=benchmark, m1_sharpe=m1_sharpe, m1_ann_return=m1_ann_return,
        prior_loops=prior_loops,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    last_err: Exception | None = None
    for attempt in (1, 2):
        t0 = time.time()
        log.info("proposer: round %d/%d, model=%s", attempt, 2, cfg.model)
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("proposer: API error attempt %d: %s", attempt, e)
            time.sleep(2)
            continue

        wall = time.time() - t0
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        cost = _cost(in_tok, out_tok, cfg.pricing_tier)

        try:
            raw = json.loads(content)
            proposal = Proposal.model_validate(raw)
            return proposal, in_tok, out_tok, cost, wall
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            log.warning("proposer: validation error attempt %d: %s", attempt, e)
            # Add the bad output back as context for the next try
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": (
                    f"Your previous response failed validation: {e}\n"
                    "Return a valid Proposal JSON, conforming exactly to the schema."
                ),
            })

    raise RuntimeError(f"proposer failed after 2 attempts: {last_err}")
