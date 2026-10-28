# The Ref prompts

The Ref is the quizmaster of the half-time games. **The code chooses, The Ref only writes**: the questions, the options,
the right answers and every number come from the games code ([`app/games/`](../../app/games/)); The Ref rewrites
the text of the questions and options in good English and in the fan's language, without adding or changing numbers.

| File | What it is |
|---|---|
| [`instructions.md`](instructions.md) | v1 instructions (who The Ref is, input, output, 7 rules, 6 examples: Tic Tac Goal, Guess the player and 4 quiz questions in English, Italian and Spanish), to paste into Foundry in the setup of The Ref |

| [`foundry_the_ref.py`](foundry_the_ref.py) | The setup of The Ref: creates the agent `the-ref` from `instructions.md` and tests it (`create`, `eval`) |
| [`foundry_test_AG4.json`](foundry_test_AG4.json) | The setup of The Ref: the 4 quiz questions of the examples in the 8 languages, version 1 |

The agent `the-ref` version 1 (`gpt-6-luna`) was created in the setup of The Ref; the server's code is
[`app/agents/the_ref.py`](../../app/agents/the_ref.py).

## What The Ref receives

One JSON object per **phase and language** (the questions are the same for every fan, so the server calls The Ref
once and keeps the text in memory, README "Il codice sceglie, l'AI scrive solo il testo"):

```json
{"language": "it", "items": [
  {"id": "QZ-1", "game": "quiz", "format": "who", "metric": "distance_covered",
   "question": "Who ran the furthest in the first half?",
   "options": [{"id": "CAL-07", "label": "Rutger van Dalfsen (CAL)"}, "..."]}
]}
```

| Field | From the games code |
|---|---|
| `language` | the fan's language (one of the app's 8: `en`, `fr`, `es`, `de`, `it`, `nl`, `zh`, `ru`) |
| `id` | quiz: the round id (`QZ-1` ... `QZ-5`, `app/games/quiz.py`); Guess the player: `GTP-1` ... `GTP-5`; Tic Tac Goal: one item for the grid |
| `game`, `format` | `tiki_taka_toe` + `grid`; `guess_the_player` + `career`; `quiz` + `who`, `when` or `how_much` |
| `metric` | the quiz question's metric (`QuizRound.metric`); context only |
| `question` | the English text written by the code |
| `options` | quiz: `QuizRound.options` (`option_id`, `label`); Guess the player: the 4 names; Tic Tac Goal: the 6 criteria (clubs and nationalities) |

**The right answer is never sent**, so The Ref cannot give it away.

## What The Ref returns

Only the same JSON shape with the text rewritten:

```json
{"language": "it", "items": [{"id": "QZ-1", "question": "Chi ha corso di più nel primo tempo?",
  "options": [{"id": "CAL-07", "label": "Rutger van Dalfsen (CAL)"}, "..."]}]}
```

## Checks for the server

Before showing The Ref's text, the server checks it; if any check fails it uses the code's English text for that item
(and if Foundry does not answer, for all of them):
1. the same items and options, with the same `id`s, in the same order;
2. **the same numbers**: the digits in the question and the options are the same as in the input (the decimal
   separator may change: `0.5` and `0,5`); the check used on the 6 examples of `instructions.md`;
3. the names of players and clubs of the options unchanged;
4. no empty text, questions of at most 120 characters.

Scoring and answers never depend on The Ref: the server still checks the fan's answer by `option_id`.

## Cost

One call per phase and language: the Games section opens only at half-time, so at most 8 calls per match (one per
language actually used), with `gpt-6-luna`. Each call carries the instructions (about 2,000 tokens, the same at the
start of every call, so Foundry can reuse them) and the 11 items (5 quiz questions, 5 Guess the player questions and
the grid, about 1,500 tokens), and returns about 1,500 tokens.

## Test of version 1

`python prompts/the_ref/foundry_the_ref.py eval --version 1`: the 4 quiz questions of the examples in one call per
language, 8 languages: **32/32 pass the server's checks** (numbers, names, options), about 3,200 tokens in and 360–460
out per call, 2.6–4.2 s. On SYN-001 at half-time through the app (DEMO tickets): the 5 real questions in Italian and
Spanish, one call per language, the second Italian fan served from memory. To read again: "Quanta percentuale di
possesso" (Italian, a little heavy).

In this version only the **quiz** goes through The Ref: in the contract the quiz is the only game whose question text
comes from the server. Guess the player has no question text from the server (only the career and 4 names, which
never change), and Tic Tac Goal's criteria are club names (never translated) and countries.

## How v1 was made

Written with Claude Code from the team's quiz rules, Archie's instructions
([`prompts/archie/`](../archie/instructions.md), as the model of style and rules) and the games code. The
examples are the questions the code builds on SYN-001 at half-time (the session's real quiz questions, the real grid
and a real Guess the player round), with the player ids checked against the universe.
