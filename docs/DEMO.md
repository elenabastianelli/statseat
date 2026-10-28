# Demo guide

How to see the whole app in about 5 minutes, on a phone or a computer.

**Demo:** https://ca-statseat-ewwubzrkf5dus.happyriver-a61de2d4.switzerlandnorth.azurecontainerapps.io/ · free and online until 10 November 2026.

## Log in

Open the demo link and type a **DEMO code**, or scan the QR code of a [DEMO ticket](../synthetic/SYN-001/tickets/README.md):

| | |
|---|---|
| `GNT-SYN001-D-WHQF-W1S6` | `GNT-SYN001-D-3WD7-9SRW` |
| `GNT-SYN001-D-Q7VD-Z8D0` | `GNT-SYN001-D-97HJ-2JME` |

Twelve DEMO codes are in the [tickets folder](../synthetic/SYN-001/tickets/README.md). Upper or lower case does not matter; type the dashes as printed.

A DEMO ticket is a gold ticket with two extras for judges:
- **the phase panel**, to jump between pre-match, first half, half-time, second half and full time;
- **the Inspector**, which shows how every answer and every statistic was made.

It works on several devices at the same time. Silver and gold tickets, as a real fan's, are bound to the first device that uses them.

**The stadium clock.** The match is replayed at 10× real time from the moment the server starts, so an ordinary ticket reaches full time after about 11 minutes. With a DEMO ticket, use the phase panel instead: each jump moves only your own session.

**Opening full time the first time** takes a few seconds, while the server computes the full-match statistics.

## A 5-minute tour

1. **Pre-match.** The two line-ups on the pitch, the squads, the stadium. Tap the star on up to 3 players: they become your favourites (confirm them in the Account page).
2. **Ask Archie** (the button with his face, always there): *"Tell me the history of the stadium."* Archie asks Kit Man, who searches the matchday documents. Turn on **Inspector** at the top to see the agents, the documents, the tokens and the cost.
3. **First half** (phase panel). **Live** shows the score and the event feed. **Data & Statistics** shows the six tables and the charts; some appear only after 5 minutes of play ("Available from 5'").
4. Ask Archie: *"Who has run the most?"*. The number comes from The Analyst's tools and is checked by the grounding validator; the button under the answer opens the Running table.
5. **Half-time.** **Games**: Tic Tac Goal, Guess the player and the quiz on the match so far. Change the language in Account first and the quiz arrives in that language (rewritten by The Ref).
6. **Full time.** The **Recap**: the story of the match, the key moments chosen by VAR and your 3 players. Download the PDF from the Account page.
7. **Fan and Analyst.** Switch the toggle at the top to **Fan**: the Analyst charts and tables are blurred ("Unlock with Gold"). Ask Archie *"What's Brackwater's PPDA?"*: he says it is only in the Analyst plan, with no number.

## Questions worth asking Archie

| Question | What to look at |
|---|---|
| *What's the score?* | The score first, from the match context; no tool needed |
| *Was the goal in the 23rd minute offside?* | The tracking check, with the margin, and the referee's decision kept |
| *Why do they call Hennebry "Diesel"?* | Kit Man and the document he used |
| *¿Quién fue el jugador más rápido?* | Archie answers in Spanish, with a decimal comma |
| *Who will win the league?* | A polite refusal: Archie talks only about today's match |
| *Show me a chart of the shots* | Archie never draws: he points to the shot map |

Archie answers in any language. The interface has 8: English, French, Spanish, German, Italian, Dutch, Chinese and Russian.

## The Inspector

With a DEMO ticket and **Inspector** on:
- under every answer of Archie: the method in one sentence, the agents with model, tokens and latency, every tool call with its arguments and result, the documents used, the grounding check and the cost;
- in Data & Statistics, "How it's computed" for every statistic: the method, the parameters, the share of data excluded and the source.

## The match

**SYN-001**, Brackwater Athletic v Calderholm United, "The Estuary Derby", Meridian League matchday 9, Lanternworks Stadium. Everything is synthetic: the clubs, the players, the officials, the stadium and the country of Meridia are invented. The final score is 2-1, with a saved penalty and a shot off the post in between.
