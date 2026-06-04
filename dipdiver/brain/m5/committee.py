"""Orchestrator: run each persona on a TradeProposal, aggregate verdicts.

LLM calls happen in parallel via threading (the openai SDK is sync; threads
give us speedup without rewriting to async). Pydantic validates each
response before it influences the aggregate decision.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
from dataclasses import dataclass

from openai import OpenAI
from pydantic import ValidationError

from dipdiver.brain.m5.personas import PERSONAS, Persona
from dipdiver.brain.m5.schema import AgentVerdict, CommitteeDecision, TradeProposal


log = logging.getLogger(__name__)


# Same pricing tiers as m2-lite (DeepSeek's price isn't in LiteLLM's registry).
_PROVIDER_PRICING = {
    "deepseek": (0.27, 1.10),     # $ / M tokens (in, out)
    "openai_gpt4o": (2.50, 10.00),
}


@dataclass(frozen=True)
class CommitteeConfig:
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "DEEPSEEK_API_KEY"
    pricing_tier: str = "deepseek"
    temperature: float = 0.3      # lower than m2-lite — we want consistent vetting, not creativity
    max_tokens: int = 800
    timeout_seconds: int = 60
    max_parallel: int = 4


def _client(cfg: CommitteeConfig) -> OpenAI:
    key = os.environ.get(cfg.api_key_env)
    if not key:
        raise RuntimeError(f"{cfg.api_key_env} not set in environment")
    return OpenAI(api_key=key, base_url=cfg.base_url, timeout=cfg.timeout_seconds)


def _cost(in_tok: int, out_tok: int, tier: str) -> float:
    if tier not in _PROVIDER_PRICING:
        return 0.0
    pi, po = _PROVIDER_PRICING[tier]
    return (in_tok / 1e6) * pi + (out_tok / 1e6) * po


def _user_prompt(proposal: TradeProposal) -> str:
    return (
        f"PROPOSED TRADE\n"
        f"  symbol:           {proposal.symbol}\n"
        f"  direction:        {proposal.direction.upper()}\n"
        f"  notional:         ${proposal.notional_usd:.2f}\n"
        f"  signal score:     {proposal.signal_score:+.4f}\n"
        f"  signal date:      {proposal.signal_date}\n"
        f"\n"
        f"UNIVERSE\n"
        f"  name:             {proposal.universe}\n"
        f"  description:      {proposal.universe_description}\n"
        f"  benchmark:        {proposal.benchmark}\n"
        f"  test window:      {proposal.test_window}\n"
        f"\n"
        f"PORTFOLIO CONTEXT\n"
        f"  current holdings: {', '.join(sorted(proposal.current_holdings)) or '(none)'}\n"
        f"\n"
        f"Apply your persona's lens. Return the JSON AgentVerdict object only."
    )


def _ask_one_persona(
    client: OpenAI, cfg: CommitteeConfig, persona: Persona, proposal: TradeProposal,
) -> tuple[AgentVerdict | None, int, int, float, Exception | None]:
    """Single LLM call, single persona. Returns (verdict, in_tok, out_tok, cost, err)."""
    messages = [
        {"role": "system", "content": persona.system_prompt},
        {"role": "user", "content": _user_prompt(proposal)},
    ]
    try:
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # noqa: BLE001
        return None, 0, 0, 0.0, e

    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    cost = _cost(in_tok, out_tok, cfg.pricing_tier)

    try:
        raw = json.loads(content)
        raw["persona"] = persona.name  # override — LLMs invent variants ("buffett_value_investor")
        verdict = AgentVerdict.model_validate(raw)
        return verdict, in_tok, out_tok, cost, None
    except (json.JSONDecodeError, ValidationError) as e:
        return None, in_tok, out_tok, cost, e


RISK_VETO_PERSONA = "risk"


def review(
    proposal: TradeProposal,
    cfg: CommitteeConfig | None = None,
    personas: tuple[Persona, ...] = PERSONAS,
) -> CommitteeDecision:
    """Run every persona against the proposal in parallel; aggregate verdicts.

    Aggregation rule (weighted veto):
      - The risk-manager persona has SINGLE-VOTE veto power. One veto from
        `risk` blocks the trade. Justification: portfolio-level concerns
        (concentration, correlation) are objectively checkable; if the
        risk persona flags it, the trade should not happen.
      - All other personas need >= 2 to agree on a veto.
      - In all cases, vetoes must outnumber non-veto verdicts.

    Fail-open: if zero valid verdicts come back (e.g. all LLM calls error),
    the trade is approved. A flaky committee shouldn't block trades.
    """
    cfg = cfg or CommitteeConfig()
    client = _client(cfg)

    verdicts: list[AgentVerdict] = []
    in_total = out_total = 0
    cost_total = 0.0

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_parallel) as exe:
        futures = {
            exe.submit(_ask_one_persona, client, cfg, p, proposal): p
            for p in personas
        }
        for fut in concurrent.futures.as_completed(futures):
            persona = futures[fut]
            verdict, in_tok, out_tok, c, err = fut.result()
            in_total += in_tok
            out_total += out_tok
            cost_total += c
            if verdict:
                verdicts.append(verdict)
            else:
                log.warning("persona %s failed: %s", persona.name, err)
    elapsed = time.time() - t0
    log.info("committee: %d/%d verdicts in %.1fs ($%.4f)",
             len(verdicts), len(personas), elapsed, cost_total)

    # Aggregate. If we got zero valid verdicts, default to APPROVE (fail-open).
    # This is a deliberate choice: a flaky committee shouldn't block trades.
    n_approve = sum(1 for v in verdicts if v.decision == "approve")
    n_veto = sum(1 for v in verdicts if v.decision == "veto")
    n_annotate = sum(1 for v in verdicts if v.decision == "annotate")

    if not verdicts:
        approved = True  # fail-open
    else:
        risk_vetoed = any(
            v.decision == "veto" and v.persona == RISK_VETO_PERSONA for v in verdicts
        )
        approved = (
            not risk_vetoed
            and (n_veto < 2)
            and (n_veto < n_approve + n_annotate)
        )

    return CommitteeDecision(
        proposal=proposal,
        verdicts=verdicts,
        approved=approved,
        n_approve=n_approve,
        n_veto=n_veto,
        n_annotate=n_annotate,
        cost_usd=cost_total,
        in_tokens=in_total,
        out_tokens=out_total,
    )
