# Fictional universe (Scout v0) and Kit Man documents

- **Date:** 2026-10-22
- **Tool:** Claude Code (Claude Opus 5.5)
- **Area:** `[DATA > Universe]` (first part of the universe work, done ahead of schedule)

## Prompt (English version)

The original task prompt was:

> Implement the Scout agent of the generator (see the project plan): generate a fictional universe (about 30 clubs, 2 full squads with 1-99 attributes from the calibration in calibration/, careers, staff, referees, a stadium with sectors and services, club history) as validated JSON, plus the markdown documents for Kit Man. Then implement Kit Man (RAG only on the documents of this match). No real names.

Because the calibration does not exist yet, the team asked to split it:

> Go ahead with part 1: generate the universe now, without the attributes, and fill in what is missing later. Update the repository accordingly (tasks, timing and next to-dos): the repository must always reflect where we are, so everyone knows our status.

## Result

- Generator: `generator/` (package `insidethegame_gen`, tests in `generator/tests/`)
- Outputs: `synthetic/universe/universe.json`, `synthetic/SYN-001/match_info.json`, `synthetic/SYN-001/kitman/*.md`, `synthetic/schema/`
- Validation report: `reports/synthetic/universe/README.md`
- Still to do: attributes 1-99 (with the attributes review) and the Kit Man agent

## Follow-up (same day)

> I updated the blocklist, can you re-validate the generated data? [...] Keep the rule.

The team extended `generator/insidethegame_gen/content/blocklist.json`. The no-smoking stadium rule was kept: prohibition phrases such as "smoke-free" are now allowed through `allowed_phrases`, and the content check was fixed to match multi-word and hyphenated phrases.

## Follow-up (same day): consistency review

> Check the generated data for inconsistencies (everything synthetic and in English). Add to the rules that substitutes on the bench can be shown cards. Make the first division 20 clubs and the second 24, like the top two English tiers, with consistent fixtures.

Changes, all in the generator sources (outputs regenerated with the same seed 2026):

- `content/league.json`: Meridian League 20 clubs / 38 rounds, Meridian Championship 24 clubs / 46 rounds (double round-robin); 14 new invented clubs (2 in tier 1, 12 in tier 2).
- `content/rules.md`: cards section now says substitutes and substituted players can be cautioned or sent off.
- `scout.py`: past-season appearances are capped at the number of rounds of the club's league (some players had 35-36 games in a 33-34 round league).
- `kitman_docs.py`: front matter titles are quoted (an unquoted `Matchday: A v B` was invalid YAML).
- Text fixes: Calderholm's "best defence in the league" and Copperhythe's "smallest ground in the top division" were false against the generated data.
- Because careers draw different random numbers, the two squads (names, ages, careers) changed; regenerating gives the same output on any machine.
