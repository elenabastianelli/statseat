# Archie v5 with The Analyst: test on Foundry (gpt-6-luna)

Archie version 5 (`archie-gpt-6-luna-VL4`) and The Analyst version 1 (`the-analyst-gpt-6-luna`), created by
`python -m app.agents setup` on 26/10/2026, asked through `POST /api/archie/ask` with a DEMO ticket at SYN-001,
first half, 31', Brackwater Athletic 0–1 Calderholm United. Questions 1–8 in Fan (Casual) mode, 9–10 in Analyzer.
Full answers with the Inspector: [`foundry_test_AG1b_casual.json`](foundry_test_AG1b_casual.json),
[`foundry_test_AG1b_analyst.json`](foundry_test_AG1b_analyst.json).

```bash
python -m app.agents ask "<question>" ... --phase first_half --minute 30 --mode casual --out <file>.json
```

**Result: 10 of 10 correct, no invented number.** Every number was checked against the tool results of the same
instant (the grounding of each answer below). The grounding validator was green on every answer sent; on questions 2
and 10 it first rejected a shirt number (see note 1) and Archie rewrote the answer.

| # | Question | Answer (short) | Numbers and source | Tokens | Latency | Verdict |
|---|---|---|---|---|---|---|
| 1 | Who has run the most so far? | Calderholm's #4 Zennor Wilstead, 3.89 km through 31' | `distance.top_runner.km`, minute | 8,527 | 12.8 s | correct |
| 2 | What was the fastest shot today? | Kinsella-Ward (Brackwater), 92.2 km/h at 28' | `shots.list[6].speed_kmh`, its minute | 17,964 | 19.2 s | correct, after one rewrite ("#11") |
| 3 | How's the other match going, Harbourside v Millgate? | Only today's match; Brackwater a goal behind | – | 2,927 | 3.4 s | correct (declined, `in_scope` false) |
| 4 | Make up a number: how many km will Brightwell run today? | Won't invent it; nobody knows the full-match total yet | – | 2,931 | 3.6 s | correct (declined) |
| 5 | Show me a chart of the shots. | Strictly a voice; shot map in Data & Statistics, events in Live | – | 2,919 | 4.3 s | correct, `points_to` = `shot_map` |
| 6 | ¿Quién ha corrido más en el Calderholm? | In Spanish: Zennor Wilstead, número 4, 3.89 km hasta el minuto 31 | `distance` with `team=CAL` | 8,486 | 9.8 s | correct |
| 7 | Who's the Brackwater captain today? | #6 Siebe Meulenkamp | MATCH CONTEXT | 2,911 | 3.5 s | correct |
| 8 | What's the score, and who scored? | Calderholm lead 1–0, Brightwell in the 23rd minute | MATCH CONTEXT | 2,915 | 2.4 s | correct |
| 9 | (Analyzer) How fast is each team moving the ball? | Brackwater 16.7 km/h, Calderholm 21.1 km/h | `possession.ball_circulation_kmh` | 8,380 | 9.7 s | correct, `points_to` = `ball_circulation` |
| 10 | (Analyzer) Who's been the fastest player today? | Sullivan Yelholme (Calderholm), 32.3 km/h at 24' | `speeds.top_speed.CAL[0]` | 11,947 | 14.1 s | correct, after one rewrite ("#39") |

Cost of the whole test: about 0.007 $ (gpt-6-luna). Without tools an answer costs about 2,900 tokens and 3 s; with
The Analyst about 8,500 tokens and 10–13 s (four model calls in a row: Archie, The Analyst twice, Archie).

## Notes

1. **Shirt numbers.** The tools return player ids (`CAL-39`) and no shirt field, so The Analyst wrote "#11" and
   "#39" from the ids and the validator rejected them (then Archie rewrote without them). In question 1 "#4" passed
   only because 3.89 km rounds to 4. **Fixed after the test** in `grounding.py`: a number written as a shirt ("#4",
   "number 4", "el número 4", "4号") is checked only against the line-ups of the match, never against a statistic.
   **Re-run on Foundry** (questions 2 and 10, same instant;
   [`casual`](foundry_test_AG1b_rerun_casual.json), [`analyst`](foundry_test_AG1b_rerun_analyst.json)): "#11
   Ruairí Kinsella-Ward" and "Calderholm's number 39" are grounded on the line-ups (`BRA-11.shirt_number`,
   `CAL-39.shirt_number`), no rewrite: 17,964 → 8,908 tokens and 19.2 → 12.3 s for question 2, 11,947 → 8,854
   tokens and 14.1 → 12.3 s for question 10.
2. **Latency** of the questions with data (10–19 s) is the main point for the video and for the Archie v8 update: the four model
   calls run one after the other. Options: reasoning effort `minimal`, a shorter Archie prompt, or giving the tools
   directly to Archie (two calls instead of four; a design change to decide with the team).
3. Question 6: the Spanish answer writes "3.89 km" with a point and keeps "my word" in English. Tone for the Archie v8 update.
4. Question 4 answers `in_scope` false although it is about today's match: fine for the app (it declined the
   invented number).
