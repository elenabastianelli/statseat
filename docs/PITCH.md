# Pitch

**TV viewers get every stat. The 60,000 people actually in the stadium get none. statseat brings explainable, personalized match intelligence to every seat, powered by a multi-agent system on Microsoft Foundry.**

## What it does

statseat is a phone app that a fan opens with the code on their match ticket. It follows the match phase by phase: line-ups and the stadium before kick-off, the live score and events during play, full statistics and charts from kick-off, games with a sponsor's prize at half-time, and a personal recap at full time, also as a PDF. At any moment the fan can ask Archie, an original old-school commentator, about the match, in any language. Every ticket gets its own experience: a simple Fan view or a full Analyst view, three favourite players, eight interface languages.

## The problem it solves

In the stadium the fan has the best view of the match and the least information about it: no replays, no statistics, no expert explaining why a moment mattered. statseat gives every seat the insight of a broadcast, adapted to who is sitting there.

## How it works

The code computes every number; the agents explain them; every number an agent says is checked. A deterministic match engine replays the synthetic match on a stadium clock and never releases the future. On Microsoft Foundry Agent Service, **Archie** orchestrates three specialists: **The Analyst** reads statistics through 12 deterministic tools that the server runs with the fan's session, **Kit Man** searches the matchday documents, and **The Ref** rewrites the half-time quiz in the fan's language. **VAR**, in code, picks the key moments for the timeline and the recap. A grounding validator checks every number in every answer, in any language, before the fan sees it.

The match itself is synthetic and measured: an invented football world of 44 clubs and a full match with tracking at 25 Hz, calibrated on aggregate statistics of open professional data, with a public realism report that shows its red lights too.

## Microsoft and Azure technologies

- **Microsoft Foundry Agent Service**: four prompt agents on `gpt-6-luna`, with function tools and file search;
- **Microsoft Agent Framework**: the agents' runs, tool calls and token budget in the server;
- **Azure Container Apps**: one container for the API and the app;
- **Azure Developer CLI (`azd`) and Bicep**: the whole system deployed with one command;
- **Managed Identity and Entra ID**: no keys anywhere;
- **Log Analytics and Application Insights**: the container logs, with Application Insights provisioned by the same Bicep template;
- **Azure Container Registry**: images built in the cloud.

## Built for production

Reliability (grounding on every number, no look-ahead, a deterministic fallback when the model does not answer), transparency (an Inspector that shows agents, tools, tokens, cost and the method behind every statistic), controls (the match comes from the ticket on the server, one device per ticket, a token budget per question, no secrets) and adoption (one `azd up`). About $0.001 per question.
