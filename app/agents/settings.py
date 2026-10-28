"""Foundry settings of Ask Archie (AG-1b), from environment variables only: no keys anywhere.

Authentication is Entra ID through `DefaultAzureCredential` (`az login` on a laptop, the managed identity of the
Container App in Azure). Without `FOUNDRY_PROJECT_ENDPOINT` and the two agent names, `POST /api/archie/ask` stays on
the contract mock. `.env.example` lists every variable; `infra/` sets them for the deploy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Global Standard price per million tokens (input, output), from the Azure price list; unknown models cost 0 in the Inspector.
PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.0, 10.0), "gpt-6-astra": (10.0, 50.0),
          "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


@dataclass(frozen=True)
class FoundrySettings:
    endpoint: str | None
    archie_agent: str | None
    archie_version: str | None
    analyst_agent: str | None
    analyst_version: str | None
    model: str = "gpt-6-luna"  # deployment of both agents unless ANALYST_MODEL says otherwise
    analyst_model: str | None = None
    kitman_agent: str = "kitman"  # AG-2: the M-4 agent with file search on the matchday documents
    kitman_version: str = "2"
    the_ref_agent: str = "the-ref"  # AG-4: rewrites the quiz questions in the fan's language (app/agents/the_ref.py)
    the_ref_version: str = "1"
    max_rounds: int = 4  # model round trips per agent and question
    max_tool_calls: int = 4  # tool calls per agent and question
    # input + output tokens per question, all agents together; AG-5: 30,000 (was 24,000) for The Analyst's parallel
    # run, about 3,000-6,000 tokens, so a Kit Man question with a grounding retry still fits
    max_tokens: int = 30000
    timeout_s: int = 30  # per model call
    prefetch_analyst: bool = True  # AG-5: The Analyst starts in parallel with Archie (ARCHIE_PREFETCH_ANALYST=0: off)

    @classmethod
    def from_env(cls) -> "FoundrySettings":
        e = os.environ.get
        return cls(
            endpoint=e("FOUNDRY_PROJECT_ENDPOINT") or None,
            archie_agent=e("ARCHIE_AGENT_ID") or None, archie_version=e("ARCHIE_AGENT_VERSION") or None,
            analyst_agent=e("ANALYST_AGENT_ID") or None, analyst_version=e("ANALYST_AGENT_VERSION") or None,
            model=e("FOUNDRY_MODEL") or "gpt-6-luna", analyst_model=e("ANALYST_MODEL") or None,
            kitman_agent=e("KITMAN_AGENT_ID") or "kitman", kitman_version=e("KITMAN_AGENT_VERSION") or "2",
            the_ref_agent=e("THE_REF_AGENT_ID") or "the-ref", the_ref_version=e("THE_REF_AGENT_VERSION") or "1",
            max_rounds=_int("ARCHIE_MAX_ROUNDS", 4), max_tool_calls=_int("ARCHIE_MAX_TOOL_CALLS", 4),
            max_tokens=_int("ARCHIE_MAX_TOKENS", 30000), timeout_s=_int("ARCHIE_TIMEOUT_S", 30),
            prefetch_analyst=e("ARCHIE_PREFETCH_ANALYST", "1").strip().lower() not in ("0", "false", "no", "off"))

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.archie_agent and self.analyst_agent)

    @property
    def analyst_deployment(self) -> str:
        return self.analyst_model or self.model


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Local runs only (`python -m app.engine`, `python -m app.agents`): KEY=VALUE lines of the repo's .env, without
    overriding the environment. The server itself never reads .env, so the tests never reach Foundry by accident."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
