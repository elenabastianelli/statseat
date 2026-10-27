# Instructions for GitHub Copilot

InsideTheGame turns synthetic match events into match intelligence for fans in the stadium: deterministic statistics, explained by AI agents on Microsoft Foundry.

## Rules

Follow [CONTRIBUTING.md](../CONTRIBUTING.md). In short:
- synthetic data only; no real people, clubs, brands or logos;
- no personal data, no secrets, no internal notes;
- English only;
- numbers come from deterministic code: agents explain them, and every number they say must come from a tool.

## Stack

- **Backend:** Python 3.12, FastAPI, Microsoft Agent Framework with Foundry Agent Service, pytest (`app/`, `data_pipeline/`, `generator/`).
- **Frontend:** React, Vite and TypeScript, as a PWA (`web/`).
- **Deploy:** Azure Container Apps with `azd up`; Bicep in `infra/`.
- **Data:** the demo runs on the synthetic match (`MATCH_SOURCE=synthetic:SYN-001`); `MATCH_SOURCE=fake` serves the sample data of the API contract.

## Conventions

- Small, typed functions.
- Agent tools run on the server and return values computed by the match engine, only for the match and the moment of the fan's session.
- Every new calculation has a test on synthetic data with hand-checked values. No real data in tests or fixtures, and tests never call Foundry: use the fake in `app/tests/fake_foundry.py`.
- Prompts used to generate significant code are saved in `prompts/`.
