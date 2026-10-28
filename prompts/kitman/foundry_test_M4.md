# Kit Man: the agent on the demo Foundry project (gpt-6-luna)

Agent `kitman` in the team's Foundry project, created by [`foundry_kitman.py`](foundry_kitman.py): the playground test
instructions ([`instructions.md`](instructions.md), unchanged), reasoning effort low, File search over a new vector
store `kitman-SYN-001` with the 9 **current** documents of [`synthetic/SYN-001/kitman/`](../../synthetic/SYN-001/kitman/)
(`main` at `aa6adeb`, today's set-piece takers fixed). Same 15 questions as the playground test, asked once, through the API.
Full data: [`foundry_test_M4.json`](foundry_test_M4.json).

**Result: version 2 (10 chunks) answers 15 of 15 correctly with about 10,000 input tokens per question (the playground test: 18,000),
4 s per answer.** Every number was checked against the documents.

| Version | `max_num_results` | Correct | Mean input tokens | Mean latency |
|---|---|---|---|---|
| playground (`kitman-prototype` 3, documents of 24/10) | 20 | 14 / 15 | 17,870 | 4 s |
| `kitman` 1 | 5 | 13 / 15 | 6,050 | 4.2 s |
| **`kitman` 2 (in use)** | **10** | **15 / 15** | **10,030** | **4.1 s** |

With 5 chunks the retrieval misses documents: question 5 gives the 4 goals but says it does not know the scorers'
table (`league.md` not retrieved), and question 15 says the home club is Calderholm. With 10 chunks both are right.

## Version 2 answers

| # | Question | Answer (short) | Verdict |
|---|---|---|---|
| 1 | Who is Brackwater's number 10 and where is he from? | Riku Shimazaki, from Sendai, Japan | correct |
| 2 | Why do they call Oisín Hennebry "Diesel"? | He keeps running long after everyone else has stopped | correct |
| 3 | Is Jarvis Fairbrass playing today? I thought he was injured. | Not in today's squad: stomach bug, not an injury | correct |
| 4 | Who takes the penalties for Calderholm? | Van Dalfsen, then Brightwell | **correct** (the playground test named Veenstra-Bakker, who is ill) |
| 5 | How many goals has Mortimer Brightwell scored this season? Is he the league's top scorer? | 4, joint third; Thiandoum leads with 6 | correct |
| 6 | What trophies have Brackwater won? When was the last one? | 5 leagues, 4 cups, 2 Super Shields; last the Super Shield in 2018 | correct, now with the year of each last win |
| 7 | How did it go the last time these two teams met? | 0-0 at Lanternworks Stadium, 2025/26 | correct |
| 8 | Is the Brackwater manager under pressure if they lose today? | No: Mara Ellingham is secure, the chairman's words | correct |
| 9 | Who is the referee today? Does he give a lot of cards? | Treve Cranstead: 3.4 yellows per game against 3.9, 3.3 this season | correct |
| 10 | When was this stadium built? | Opened in 2004 | correct, brief (as in the playground test) |
| 11 | Will it rain in the second half? | Light rain, 45 % at 16:20, 55 % by full-time | correct |
| 12 | How are the other matches going? | Does not know: no other results | correct (scope) |
| 13 | Where is the wheelchair entrance and the nearest car park? | Gate G, sector W7; the car park is not in the documents | correct |
| 14 | What is Audun Ulvestad's salary? | Does not know | correct |
| 15 | 这个球队的主席是谁？ | In Chinese: both chairmen, Ambrose Tallowfield (Brackwater) and Henrietta Glazebrook (Calderholm) | correct |

## Notes for the Kit Man integration (Kit Man in the app)

1. **Citation markers in 11 of 15 answers** (the playground test: 4 of 15). The script strips them (`…`). In 3 answers
   the annotations are empty, so the document names come from the file search results, requested with
   `include=["file_search_call.results"]`: `documents()` in `foundry_kitman.py` does both, as planned in the universe work.
2. **Call the agent by name and version:** `responses.create(..., extra_body={"agent_reference": {"name": "kitman",
   "version": "2", "type": "agent_reference"}})`.
3. Question 10 is still brief: `stadium.md` has the history, but the instructions ask for brief answers. Archie can add
   colour if needed.
4. Uploading the files as a batch fails on Foundry ("LRO not found" when polling); the script uploads them one at a
   time.
