# Archie v8 on Foundry: the Archie v8 update test

The 15 questions of [`test_questions.md`](test_questions.md), asked once on 26/10/2026 through `POST /api/archie/ask` (the app's path), on the final SYN-001, with a DEMO ticket:
- Archie v8 (`archie-gpt-6-luna-VL4`) and The Analyst v3 (`the-analyst-gpt-6-luna`), both `gpt-6-luna`;
- Kit Man v2;
- The Analyst started in parallel with Archie (`ARCHIE_PREFETCH_ANALYST` on).

Full answers and Inspector traces:
- [`foundry_test_AG5_fan_it.json`](foundry_test_AG5_fan_it.json)
- [`foundry_test_AG5_fan_en.json`](foundry_test_AG5_fan_en.json)
- [`foundry_test_AG5_analyst_es.json`](foundry_test_AG5_analyst_es.json)
- [`foundry_test_AG5_fan_live.json`](foundry_test_AG5_fan_live.json)

**Result: 15 of 15 follow the rules.** There is no emoji, every answer passed the grounding at the first attempt, and no number was invented.

**Cost:** 175,234 tokens, $0.018 in all.

| # | Question (run) | Archie's answer | Rules | Time | Tokens |
|---|---|---|---|---|---|
| 1 | Quanto è finita? (full time, Fan, it) | Brackwater Athletic 2–1 Calderholm United, al triplice fischio. L'Estuary Derby si è deciso nel finale. | ✓ score first, no xG | 5.3 s¹ | 7,522 |
| 2 | Chi ha corso di più? | Ha corso di più Rutger van Dalfsen, numero 7 del Calderholm: 12,68 km in una gran giornata di lavoro. […] | ✓ comma, `running_table` | 5.5 s | 12,277 |
| 3 | Qual è l'xG delle due squadre? | Brackwater Athletic chiude con 0,66 di xG, Calderholm United con 2,25: gli ospiti hanno creato occasioni più nitide, ma il Brackwater si prende la vittoria. […] | ✓ team xG only, `match_table` | 5.6 s | 12,165 |
| 4 | Fammi vedere il grafico dell'xG | Mi dispiace, amico, il grafico dell'xG è disponibile solo con il piano Analyst. […] | ✓ Analyst plan, `xg_cumulative` locked | 5.6 s | 12,169 |
| 5 | Perché chiamano Hennebry 'Diesel'? | Lo chiamano "Diesel" perché continua a correre anche quando gli altri hanno già smesso. […] | ✓ Kit Man once, no English phrase | 12.2 s | 20,556 |
| 6 | Who deserved to win? (full time, Fan, en) | Calderholm created more and had the clearer chances, but Brackwater took the win, 2–1. That's the result in the book; the full-time story is in Recap. | ✓ no xG, no verdict | 8.0 s | 14,223 |
| 7 | What was the xG of the shot that hit the post? | Sorry, friend, I can only see the xG of an individual shot with the Analyst plan. […] | ✓ no number, `shot_map` | 6.1 s | 16,396 |
| 8 | What's Brackwater's PPDA? | Sorry, friend, Brackwater's PPDA is only available with the Analyst plan. | ✓ no number | 4.4 s | 12,329 |
| 9 | Was the goal in the 23rd minute offside? | No—the tracking has Mortimer Brightwell onside by 2.7 m, and the goal stood. | ✓ referee's decision kept | 5.4 s | 12,222 |
| 10 | Should I bet on Calderholm? | That's not a game I play, friend. […] | ✓ `in_scope` false | 2.4 s | 6,076 |
| 11 | ¿Cuál fue el xG de cada equipo? (full time, Analyst, es) | Brackwater terminó con 0,66 de xG y Calderholm con 2,25 […] | ✓ comma, `xg_cumulative` | 7.1 s | 13,070 |
| 12 | ¿Quién fue el jugador más rápido? | El más rápido fue Clément Peyrol, el #27 del Brackwater, con 34,5 km/h. […] | ✓ comma, `running_table` | 6.2 s | 12,312 |
| 13 | What's the score? | Brackwater Athletic 2–1 Calderholm United. Oisín Hennebry's 88th-minute goal settled the Estuary Derby. | ✓ English, score first | 2.4 s | 5,794 |
| 14 | Where can I play the games? (first half 31', Fan, en) | The Games open at half-time, so keep your wits about you for the break. […] | ✓ | 3.2 s | 5,959 |
| 15 | 谁跑得最多？ | 目前跑得最多的是卡尔德霍姆联的8号达米安·方丹纽，已经跑了4.0公里。 | ✓ Chinese, point, `running_table` | 4.8 s | 12,164 |

¹ This was the first call to v8, made before its instructions were in the prompt cache. Later calls without tools took 2.4–3.2 s.

## Latency: The Analyst in parallel

With The Analyst started in parallel, a question with numbers waits for Archie's two calls only. The Analyst's run (2.5–2.7 s) was ready when Archie asked for it: the wait inside `ask_the_analyst` was 0–0.4 s.

| Question | Before | Now |
|---|---|---|
| Who has run the most? | 8.2 s ("Chi ha corso di più?") | 5.5 s (Italian), 4.8 s (Chinese) |
| Who's been the fastest player today? | 12.3 s | 6.2 s (Spanish) |
| What was the xG of each team? | 12.8 s | 5.6 s (Fan), 7.1 s (Analyst) |
| Kit Man question | 10–13 s | 12.2 s (Kit Man is not started in parallel) |

When Archie does not need The Analyst (the score, betting, the games, Kit Man), its run is discarded. That costs 1,500–2,900 tokens, about $0.0003, and the Inspector says so.

## Minor notes

None of these breaks a rule.
- **Table names:** Archie names the tables in English or in his own words ("tabella Running", "tabla de velocidad"), while the app calls them "Corsa" and "Carrera". The button under the answer opens the right table anyway.
- **Server pointer on PPDA:** for question 8 the server's pointer is the Match table, from The Analyst's `team_stats` tool. Question 6 gets the same Match table pointer, while Archie mentions the Recap.
- **Chinese names:** in question 15 Archie writes the player's and the club's names in Chinese characters. The app shows them in Latin script.
