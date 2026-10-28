# First quality-control pass on DFL match J03WPY

- **Date:** 2026-10-21 (first run, guidelines v2); re-run on 2026-10-22 with guidelines v3
- **Tool:** Claude Code, `data-analyst` subagent
- **Area:** `[DATA > Quality control]`

## Prompt (English version)

> Use the data-analyst subagent. Create the data_pipeline/ folder with a Python 3.12 environment (venv) and the required packages (kloppy or floodlight, numpy, pandas, scipy, pytest). Follow docs/DATA_ANALYSIS_GUIDELINES.md: build the match profile and all the checks of sections 3, 5 and 6 for match J03WPY (files in data/raw/dfl/). Write the report in reports/qc/J03WPY/README.md and then explain the results to me in plain Italian. Save the English version of this prompt in prompts/.

## Re-run with guidelines v3 (2026-10-22)

Follow-up prompt (English version):

> Re-running the same prompt with the updated data analysis guidelines.

The guidelines moved to v3 (see "What changed in v3" in `docs/DATA_ANALYSIS_GUIDELINES.md`), so the same prompt was run again. The existing pipeline was updated rather than rewritten. Main changes:
- **Kinematics.** Always computed from cleaned positions with Savitzky-Golay; the provider's speed, acceleration and step are used only in tests.
- **Physical limits** from `Materials/PHYSICAL_LIMITS.md`: player speed at most 38 km/h, player acceleration between -8.5 and +7.5 m/s², ball speed at most 211 km/h in live play.
- **Gaps.** A gap is estimated only when the player resumes less than 6 m away; otherwise it is discarded. Distances get "≈" when part of the time is not measured.
- **Substitutions.** Identity inheritance is applied.
- **Edges.** Tracking-area edges are discovered for each match.
- **Ball status.** It is never derived from a stationary ball.
- **Codes.** HL/HR and DLM/DRM are mapped to English through a reviewed mapping file.

The pass/warn/fail checks were replaced by the automatic handling log of section 5 and the validity mask of section 6.

## Result

- **Code.** `data_pipeline/` (package `insidethegame_data`, tests in `data_pipeline/tests/`, mapping in `data_pipeline/reference/dfl_to_english.yaml`).
- **Report.** `reports/qc/J03WPY/README.md` (preparation report v3), plus `match_profile.json` and `qc_results.json`.
- **Validity.** 98.04 % of player samples are valid for kinematics.
  - Tracking-area edge discovered at x = ±55.5 m (1,610 samples, 8 runs).
  - 191 jump episodes, 26 swap incidents and 543 frozen runs handled.
  - 9 of 9 substitutions show identity inheritance.
  - Valid peaks: 33.5 km/h for players and 119.7 km/h for the live ball.
- **Tests.** 53 pytest tests pass, and two runs give byte-identical outputs.
