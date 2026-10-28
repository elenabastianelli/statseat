# Review of the v4 report and analyzer v4.2

- **Date:** 2026-10-25
- **Tool:** Claude Code (Claude Opus 5.5), with the `data-analyst` subagent
- **Area:** `[DATA > Review]`

## How it went

1. **Review with the data lead.** The v4 run on the six DFL matches left 11 doubtful episodes (unconfirmed goals, a fake disallowed goal, shots "away from goal" or "directed at a team-mate", woodwork, headers, a parking value). Claude drew one figure per episode and wrote the facts; the data lead looked at each figure and decided the rule. The data lead's rules: a goal needs the ball to cross the goal line between the posts; a ball frozen by the provider is extrapolated only when no player is within 1 m; a goal at the end of the period counts only without a VAR, disallowed-goal, foul or offside event before the final whistle.
2. **Plan approved by the data lead**, then the prompt below to the `data-analyst` subagent.
3. **The data lead's review of the rerun**, then three more decisions (0.5 m nearest-player margin for aerial touches, the net is not the woodwork, one late-annotated shot left open) and the calibration step.
4. **Two calibration decisions:** the quantile method stays as it is (open CC-BY aggregates), and shot speed is computed without headers everywhere, as decided in the metrics review.

## Prompt to the data-analyst subagent (condensed)

> You are implementing the v4 review: turning the v4 analyzer into v4.2 (guidelines + code + tests), then re-running it on
> the 6 DFL matches and comparing with the v4 baseline. The data lead has approved the plan; the rules below are decided.
> Work only in the worktree. Edit existing files only with the Edit tool; no commits; do not touch app/ or generator/;
> never rename output fields; do not regenerate calibration/ yet. Every new rule has an R-... name, is written in the
> guidelines, has its parameters in config.py and at least one test.
>
> Phase A - guidelines v4.2: write the 12 rules v4.1 (goal at the end of the period with the data lead's three conditions;
> attribution with a deflection; "directed at a team-mate" only if he receives; power above the DFL pass p99 confirms
> a shot except headers; 8 m / 6 m direction margin; blocks in flight; header thresholds 10 deg and 2 m/s2; woodwork
> without "no player within 2 m"; clearance C4 = an opponent within 4 m; parking values need 3 players;
> acceleration window 19 samples; penalties in the shot statistics), the woodwork path rule, and the rules from the
> episodes: map the 7 DFL types outside the 23; disallowed_reason from GoalDisallowed; the entry rule (a ball that
> went out stays out until it is back on the pitch for 0.2 s); R-GOAL-FREEZE (at most 2 frames, nobody within 1 m);
> backward shot search on unconfirmed crossings; search up to the crossing frame; R-TOUCH-NEAREST (nearest player or
> within 0.3 m of the nearest, all kicks; ground touches below 1.5 m; away-from-goal with an opponent within 1 m =
> blocked); the woodwork decision (path within 0.3 m and players at least 1 m away, or closer to the frame than to any
> player by 0.5 m; 3-D above 2.3 m); R-LATE-ANNOTATION (mostly dead window: search back up to 20 s); an aerial duel
> with WinnerAction=shot may share the shot frame. Move the general ball-spike rule and the soft aerial flick to the
> open points. Add section 21 "Changes from v4".
>
> Phase B - code and tests in data_pipeline/, plus anonymous labels in every DFL report (team side, role group,
> number; the label-ID map only in the git-ignored frames folder; a test that no provider ID appears), the xG model
> logit(xG) = a + 2.84*theta + b*n with the angle slope fixed (Soccermatics) and a, b fitted on the DFL shots, in
> the JSON format the generator already reads, and attribute_scales with 21 stored quantiles and 99 interpolated
> values. Run the pipeline, generator and app tests.
>
> Phase C - rerun the six DFL matches and SYN-001, rewrite the SUMMARY analyst notes, and write a local before/after
> comparison with the 11 episodes one by one. Final report: files, tests, before/after table, located passes,
> proposed text for principles 7, 8 and 10 of the subagent, deviations and questions for the data lead.

## Result

- Guidelines v4.2 (`docs/DATA_ANALYSIS_GUIDELINES.md`, section 21), analyzer v4.2 in `data_pipeline/` with a test for every rule.
- Six DFL matches: 16 of 16 annotated goals confirmed (v4: 13), 104 of 144 shots confirmed (v4: 84), 24 of 29 header shots located (v4: 20), located passes 95.2 %; reports with anonymous labels in `reports/qc/`.
- Calibration rebuilt with the new xG (logit(xG) = -3.19 + 2.84*theta - 0.91*n) and shot speed without headers; realism report re-run.
