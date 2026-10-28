# Archie prompts

Archie is the stadium app's voice and orchestrator: an original, old-school British commentator. He answers only when asked, never draws charts, and never says a number that does not come from a tool or from the server's match context.

| File | What it is |
|---|---|
| [`instructions.md`](instructions.md) | v9 instructions: style guide, the Fan and Analyst versions, numbers and xG, scope, referees, app sections and charts, 19 few-shot examples, a sample match context block |
| [`app_addendum.md`](app_addendum.md) | The first Foundry integration, the Kit Man integration, the Archie v8 update: the block appended in the app (the `ask_the_analyst` and `ask_kit_man` tools, the MATCH CONTEXT and the fan's words, the JSON reply and the table of each number); `python -m app.agents setup` builds the agent from both files |
| [`test_questions.md`](test_questions.md) | The 15 test questions of v8, with the commands and the expected behaviour |
| [`foundry_test_v9_fan.json`](foundry_test_v9_fan.json), [`foundry_test_v9_analyst.json`](foundry_test_v9_analyst.json) | Archie v9 on Foundry: the defensive line question on a Fan and on an Analyst ticket, at full time (2 of 2 follow the rules) |
| [`foundry_test_AG5.md`](foundry_test_AG5.md) | The Archie v8 update: Archie v8 and The Analyst v3 on Foundry, the 15 questions through the app's endpoint (15 of 15 follow the rules), with latency before and after The Analyst started in parallel |
| [`foundry_test_AG1b.md`](foundry_test_AG1b.md) | The first Foundry integration: Archie v5 with The Analyst on Foundry, 10 questions through the app's endpoint (10 of 10 correct, no invented number) |
| [`foundry_test_AG2_kitman.json`](foundry_test_AG2_kitman.json) | The Kit Man integration: Archie v7 with Kit Man, 5 questions |
| [`foundry_test_VL4.md`](foundry_test_VL4.md) | The first playground test: Archie v1 in the Foundry playground, no tools |

## How v1 was made

v1 fixes the three notes from the first Playground test (GPT-6 Luna):
1. Web Search is removed from Archie's tools: scope risk and about 4,400 input tokens per question.
2. The app sections and when they unlock are listed with their exact names (the first playground test invented a "Statistics" section).
3. The personality was flat: v1 adds a style guide and 12 original examples (no fine-tuning).

Team decision: Archie may joke about refereeing in general, with affection, but never mocks a specific official or questions a decision.

The character is inspired only by a general style (the old-school British football commentator). No real person is named, quoted or imitated, and no real broadcast catchphrase is used.

## Prompt used to generate v1 (Claude Code)

> Write Archie's instructions in prompts/archie/ (in English), an ORIGINAL character: an old-school British commentator with a sheepskin coat and a microphone, enthusiastic, full of anecdotes, with gentle irony and period expressions. Do not quote, name or imitate real people; do not use phrases actually said by commentators. Include: (1) the style guide; (2) 12 original question-and-answer examples, in Casual and Analyst mode; (3) the list of app sections and when they unlock (Pre-match always; Live during play; Data & Statistics, Games and Polls at half-time and after the match; Recap after the match); (4) the rules: reactive only, no charts (point to the right section), never invented numbers, only the ticket's match. Then write 15 test questions. Give me the full text to paste into Foundry.

In the app ([`app/agents/`](../../app/agents/README.md)), the `MATCH CONTEXT` block is built by the server from the ticket (`match_id` is never chosen by the model) and sent before the fan's words; data results come from The Analyst ([`prompts/analyst/`](../analyst/instructions.md)) and, with the Kit Man integration, Kit Man; the grounding validator checks every number of the answer.

## How v8 was made

The live tests of the first Foundry integration, the Data & Statistics screens and the Kit Man integration collected the notes on Archie's tone. the Archie v8 update fixed them all at once, with one new version (v8) and one test of 15 questions:
1. **No emojis.** v1 had 🎙️ as a signature, and it appeared in 3 of 5 answers in the Kit Man integration.
2. **English period phrases only in English.** "My word" had shown up in Spanish and Italian answers.
3. **Decimals in the fan's language:** a comma in Italian, Spanish, French, German, Dutch and Russian; a point in English and Chinese.
4. **The score first.** The score question used to open with the xG.
5. **xG only when the fan asks for it.** A Fan ticket gets the xG of the two teams only, which is the one xG that the Fan version shows. Otherwise the xG only helps Archie read the result in words, with no "lucky", "clinical" or verdict.
6. **The Analyst plan.** For anything that the Fan version does not show, Archie says with regret that he can only see it with the Analyst plan, and gives no number. The server lists the locked tables and charts in the MATCH CONTEXT.
7. **Key moments** point to the Live timeline and the Recap: Archie does not query VAR.
8. **The table of each number**, with the server as a safety net when Archie leaves `chart` empty.

The gold version was renamed **Analyst** (it was "Analyzer"), so The Analyst also got a new version, v3, with the new name in its instructions and tool descriptions. In the same task the server started The Analyst in parallel with Archie, which brings a question with numbers from 8–13 s down to 4–8 s ([`app/agents/README.md`](../../app/agents/README.md)).

## Prompt used for the Archie v8 update (Claude Code)

> One new version of Archie with every note collected in the first Foundry integration, the Data & Statistics screens and the Kit Man integration. Rules decided by the team:
> - no emojis;
> - English interjections only in English;
> - decimals in the fan's language;
> - the score first;
> - say "xG" only when the fan asks for it: on a Fan ticket only the xG of the two teams, on an Analyst ticket what the fan asks for, with the xG chart;
> - for anything the Fan version does not show, answer with regret that it is only available with the Analyst plan ("Sorry, friend, I can only see that one with the Analyst plan"), with no number;
> - key moments point to the Live timeline and the Recap (Archie does not query VAR);
> - the server points to the table of The Analyst's numbers when Archie gives none.
>
> Rename the gold version from "Analyzer" to "Analyst" everywhere. Cut the waiting time on questions with numbers. Rewrite the 15 test questions for the tools, run them once on Foundry after my yes, and save the answers.

## How v9 was made

A review of the app found two gaps in Archie's answers about the charts:
1. **Defensive line and team length.** The Analyst has no number for them, and Archie did not say where to look. v9 points to the "Defensive line and team length" chart (`defensive_shape`) in Data & Statistics, and on a Fan ticket says with regret that the chart is in the Analyst plan only. One new example (19).
2. **Acceleration load.** Its data are not reliable enough, so the app no longer shows the chart. Archie never uses, quotes or points to it, even when a data result contains it; the server drops `acceleration_load` from the chart ids of the response format and from the locked list of a Fan ticket's MATCH CONTEXT.

Two questions on Foundry, at full time, through the app's endpoint:

| Ticket | Question | Answer | Chart |
|---|---|---|---|
| Fan | How high was Calderholm's defensive line? | "That's one for the "Defensive line and team length" chart in Data & Statistics. Sorry, friend, it's only available with the Analyst plan." | `defensive_shape`, locked |
| Analyst | How high was Calderholm's defensive line, and how long was the team? | "That's one for the "Defensive line and team length" chart in Data & Statistics: it shows where Calderholm's back line sat and how compact the team was." | `defensive_shape` |

No tools called, no number, grounding passed; 5–6.5 s and under 8,000 tokens per question.
