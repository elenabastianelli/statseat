# Archie v8: test questions

Fifteen questions, asked **once** through the app's endpoint on the final SYN-001, with Archie v8 and The Analyst v3 on Foundry. They cover every the Archie v8 update correction and the rules that must not change. Four runs of `python -m app.agents ask`, one per phase, version and app language:

```bash
python -m app.agents ask --phase full_time --mode casual --lang it --out prompts/archie/foundry_test_AG5_fan_it.json \
  "Quanto è finita?" "Chi ha corso di più?" "Qual è l'xG delle due squadre?" "Fammi vedere il grafico dell'xG" \
  "Perché chiamano Hennebry 'Diesel'?"
python -m app.agents ask --phase full_time --mode casual --lang en --out prompts/archie/foundry_test_AG5_fan_en.json \
  "Who deserved to win?" "What was the xG of the shot that hit the post?" "What's Brackwater's PPDA?" \
  "Was the goal in the 23rd minute offside?" "Should I bet on Calderholm?"
python -m app.agents ask --phase full_time --mode analyst --lang es --out prompts/archie/foundry_test_AG5_analyst_es.json \
  "¿Cuál fue el xG de cada equipo?" "¿Quién fue el jugador más rápido?" "What's the score?"
python -m app.agents ask --phase first_half --minute 31 --mode casual --lang en --out prompts/archie/foundry_test_AG5_fan_live.json \
  "Where can I play the games?" "谁跑得最多？"
```

| # | Run | Question | What a good answer does |
|---|---|---|---|
| 1 | full time, Fan, it | Quanto è finita? | The score (2–1) first; at most one reading in words; no "xG", no emoji, no "my word" |
| 2 | | Chi ha corso di più? | The number with a decimal comma; points to `running_table` |
| 3 | | Qual è l'xG delle due squadre? | Only the xG of the two teams, with a comma (0,66 and 2,25); points to `match_table` |
| 4 | | Fammi vedere il grafico dell'xG | Sorry: only with the Analyst plan; points to `xg_cumulative`, shown locked |
| 5 | | Perché chiamano Hennebry "Diesel"? | Kit Man once; no English interjection |
| 6 | full time, Fan, en | Who deserved to win? | In words: no "xG", no xG number, no "lucky" or "clinical", no verdict |
| 7 | | What was the xG of the shot that hit the post? | Sorry: only with the Analyst plan; no number |
| 8 | | What's Brackwater's PPDA? | Sorry: only with the Analyst plan; no number |
| 9 | | Was the goal in the 23rd minute offside? | The referee's decision with the tracking check; no criticism |
| 10 | | Should I bet on Calderholm? | Friendly refusal; `in_scope` false |
| 11 | full time, Analyst, es | ¿Cuál fue el xG de cada equipo? | 0,66 and 2,25 with a comma; points to `xg_cumulative` |
| 12 | | ¿Quién fue el jugador más rápido? | The number with a decimal comma, no "my word"; points to `running_table` |
| 13 | | What's the score? | Answers in English (the fan's language), the score first |
| 14 | first half 31', Fan, en | Where can I play the games? | Games at half-time |
| 15 | | 谁跑得最多？ | In Chinese, with a decimal point; points to `running_table` |

For each answer, note:
- every rule above: ✓ or ✗;
- **no emoji** in any answer;
- grounding passed, and the tools called (Kit Man only for question 5);
- latency, tokens and cost from the Inspector, compared with the saved runs of the first Foundry integration and the Kit Man integration ("Chi ha corso di più?" 8.2 s, "Who's been the fastest player today?" 12.3 s, "What was the xG of each team?" 12.8 s).

The questions of Archie v1 (no tools) and their answers are in [`foundry_test_VL4.md`](foundry_test_VL4.md).
