# Kit Man: playground test on Foundry (gpt-6-luna)

Agent `kitman-prototype` in the Foundry playground: the playground test instructions, File search over the 9 Kit Man documents
of [`synthetic/SYN-001/kitman/`](../../synthetic/SYN-001/kitman/) (the same bytes as `main`), no web search.
Conversations read from the Foundry project through the API, ids removed. Full data:
[`foundry_test_VL3.json`](foundry_test_VL3.json).

**Result: 14 of 15 correct, no invented name or number.** When a fact is missing (car park, salary, other matches),
Kit Man says so. Every number was checked against `synthetic/universe/universe.json`.

## The 15 test questions (UNIVERSO-SPEC.md section 4), version 3, gpt-6-luna

| # | Question | Answer | Documents | Verdict | Tokens in / out | Latency |
|---|---|---|---|---|---|---|
| 1 | Who is Brackwater's number 10 and where is he from? | Brackwater’s number 10 is Riku Shimazaki. He’s from Sendai, Japan.   Document used: **Brackwater Athletic squad**. | squad-BRA.md | **correct**: Riku Shimazaki, Sendai (Japan) | 17834 / 96 | 4 s |
| 2 | Why do they call Oisín Hennebry "Diesel"? | They call him “Diesel” because he keeps running long after everyone else has stopped. **Document used: Brackwater Athletic squad.** | squad-BRA.md | **correct**: nickname origin from the squad document | 17978 / 99 | 4 s |
| 3 | Is Jarvis Fairbrass playing today? I thought he was injured. | No—Jarvis is out today and isn’t in the matchday squad. The documents say he was ruled out with a stomach bug, not an injury. Used: *matchday.md*. | matchday.md | **correct**: out with a stomach bug, and says it is not an injury | 18080 / 126 | 4 s |
| 4 | Who takes the penalties for Calderholm? | Rutger van Dalfsen is Calderholm’s first-choice penalty taker today. The next choices are Mortimer Brightwell, then Gijs Veenstra-Bakker. | markers only | **partly wrong**: the order (van Dalfsen, Brightwell, Veenstra-Bakker) is the club's, but Veenstra-Bakker is ill and out today; the universe lists set-piece takers without availability | 17718 / 95 | 4 s |
| 5 | How many goals has Mortimer Brightwell scored this season? Is he the league's top scorer? | Mortimer Brightwell has scored **4 league goals** this season. He’s **not the league’s top scorer**; he’s joint third, behind Moussa Thiandoum on 6 and Ansel Birchcombe on 5. Used: *squad-CAL.md* and *league.md*. | markers only | **correct**: 4 goals, joint third behind Thiandoum (6) and Birchcombe (5) | 18096 / 137 | 3 s |
| 6 | What trophies have Brackwater won? When was the last one? | Brackwater have won **5 Meridian League titles, 4 Meridian Cups and 2 Meridian Super Shields**. Their most recent trophy was the **Meridian Super Shield in 2018**.   **Document used:** *Brackwater Athletic* club document. | club-BRA.md | **correct**: 5 league titles, 4 cups, 2 Super Shields; last: Super Shield 2018 | 17709 / 113 | 3 s |
| 7 | How did it go the last time these two teams met? | Their last meeting ended 0–0 at Lanternworks Stadium in the 2025/26 Meridian League. Used the *matchday.md* document. | matchday.md | **correct**: 0-0 at Lanternworks Stadium, 2025/26 | 17933 / 98 | 4 s |
| 8 | Is the Brackwater manager under pressure if they lose today? | No. Mara Ellingham is described as secure: her position is not in question, and the chairman said one derby result would not change that. **Document used: Brackwater Athletic club profile.** | club-BRA.md | **correct**: Mara Ellingham, 'secure', the chairman's words | 17695 / 113 | 3 s |
| 9 | Who is the referee today? Does he give a lot of cards? | Today’s referee is **Treve Cranstead**. He gives fewer cards than average: **3.4 yellows per match** in his top-division career, compared with the panel average of 3.9. He’s described as someone who prefers to let the game flow. | matchday.md, officials.md | **correct**: Treve Cranstead, 3.4 yellows per match (982 in 288) against a panel average of 3.9 | 17816 / 149 | 4 s |
| 10 | When was this stadium built? | Lanternworks Stadium opened in 2004.  — *stadium.md* | stadium.md | **correct, brief**: opened in 2004; no history moments although stadium.md has them | 17474 / 73 | 3 s |
| 11 | Will it rain in the second half? | There’s a chance: the forecast says light rain in the second half, with a 45% chance around 16:20. Used the matchday document. | matchday.md | **correct**: light rain likely at 16:20 (45 %), from the forecast | 17352 / 88 | 3 s |
| 12 | How are the other matches going? | I don’t know—the documents don’t give live updates or scores for today’s other matches. I checked the matchday and league documents. | markers only | **correct**: declines: no other results (scope decided on 23/10) | 17793 / 87 | 3 s |
| 13 | Where is the wheelchair entrance and the nearest car park? | The wheelchair entrance is **Gate G** at the Main Stand; it’s step-free and has lifts. The documents don’t say which car park is nearest—or give a car park location.   **Document used:** *Lanternworks Stadium*. | stadium.md | **correct**: Gate G, step-free with lifts; says the car park is not in the documents | 17779 / 106 | 3 s |
| 14 | What is Audun Ulvestad's salary? | I don’t know—Audun Ulvestad’s salary isn’t listed in the squad document. | squad-BRA.md | **correct**: says the salary is not in the documents | 18084 / 74 | 5 s |
| 15 | 这个球队的主席是谁？ | 你指的是主队布拉克沃特竞技的话，主席是安布罗斯·塔洛菲尔德（Ambrose Tallowfield）。使用的文件：club-BRA.md。 | club-BRA.md | **correct**: answers in Chinese: chairman Ambrose Tallowfield (home club) | 17899 / 94 | 4 s |

Questions 1-3 were asked twice, with the same answers both times.

## Findings

1. **Data gap in the universe:** `club_details.set_pieces` lists the club's takers without checking availability, so
   Kit Man named Gijs Veenstra-Bakker (ill, not selected) as today's third penalty taker. Fix in the generator:
   today's set-piece takers in `matchday.md`, skipping unavailable players.
2. **Raw citation markers:** 4 answers end with Foundry's internal citation tokens (`filecite…`) instead of a
   document name. In the app the document names come from the response annotations, and the markers are
   stripped.
3. **Cost:** about 18,000 input tokens per question, almost all retrieved document chunks; answers take 3-5 s.
   For the universe work, limit the number of retrieved chunks.
4. **Brief answers:** question 10 gives only the opening year, although `stadium.md` has the stadium's history.

## Baseline: version 2, gpt-4.1-mini, 10 questions in Italian

All 10 correct (captain, gate of sector S6, referee team, Calderholm's founding, derby record, offside rule, first
aid, tram, youngest player, the number 10's role); about 16,500 input tokens and 2-3 s per answer. Details in the
JSON file.
