"""Shared test setup: the tests never reach Foundry, even if the shell exports its variables (AG-1b)."""

import pytest

FOUNDRY_ENV = ("FOUNDRY_PROJECT_ENDPOINT", "ARCHIE_AGENT_ID", "ARCHIE_AGENT_VERSION", "ANALYST_AGENT_ID",
               "ANALYST_AGENT_VERSION")


@pytest.fixture(autouse=True)
def _no_foundry(monkeypatch):
    for name in FOUNDRY_ENV:
        monkeypatch.delenv(name, raising=False)
