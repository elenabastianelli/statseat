"""Data preparation parameters (guidelines v4.2, section 15).

Every value here is a *threshold or method parameter*, never a property of a
match. Match properties (sampling rate, pitch size, origin, frame offsets,
playing direction, tracking-area edges, parking values, event clock offset) are
discovered per match in `profile.py`, `edges.py` and `timealign.py`. Physical
limits come from `Materials/PHYSICAL_LIMITS.md`.

The values marked with a dagger (†) in guidelines v4 were reviewed after the v4 run
(E-1, 25 October 2026): the ones touched by the v4.1 / v4.2 rules changed, the others
were confirmed. No value is provisional any more.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

GUIDELINES_VERSION = "v4.2 (2026-10-25)"
# Sub-folder of the git-ignored frame-level outputs (reports/qc/frames/<id>/<FRAMES_VERSION>/). A new version
# never overwrites the frame-level files of an earlier analyzer run.
FRAMES_VERSION = "v4.2"


def odd_window(fs_hz: float, fraction: float, polyorder: int) -> int:
    """odd(round(fs * fraction)), never shorter than the smallest window the polynomial order allows."""
    w = int(math.floor(fs_hz * fraction + 0.5))
    if w % 2 == 0:
        w += 1
    min_w = polyorder + 1 if polyorder % 2 == 0 else polyorder + 2
    return max(w, min_w)


@dataclass(frozen=True)
class PrepConfig:
    guidelines_version: str = GUIDELINES_VERSION

    # --- Savitzky-Golay, the only filter (section 7.2) ---
    savgol_polyorder: int = 2
    savgol_window_fraction_of_fs: float = 0.5  # R-SG-SPEED: odd(round(fs * 0.5)) -> 13 samples at 25 Hz (speed, distance, ball)
    # R-SG-ACC (v4.1): player acceleration window chosen by the team after the v4 sensitivity test (25 Oct 2026):
    # 0.75 -> 19 samples at 25 Hz. Velocity, speed and distance stay at 13 samples.
    acc_window_fraction_of_fs: float = 0.75
    acc_window_test_fractions: tuple[float, ...] = (0.5, 0.75, 1.0)  # R-ACC-WINDOW-TEST

    # --- Physical limits and plausibility band (section 8, Materials/PHYSICAL_LIMITS.md) ---
    player_speed_max_kmh: float = 38.0  # R-SPEED-LIMIT (outlier)
    player_acc_min_ms2: float = -8.5  # R-ACC-BAND: tangential (braking); a band, never an exclusion
    player_acc_max_ms2: float = 7.5  # R-ACC-BAND: tangential (accelerating)
    acc_noise_max_s: float = 0.2  # R-ACC-BAND: an exceedance lasting <= 0.2 s (5 samples at 25 Hz) is noise
    ball_speed_max_kmh: float = 211.0  # R-BALL-LIMIT: live play only; no limit on ball acceleration

    # --- Exclusion windows (section 6.2) ---
    exclusion_window_s: float = 1.0  # players: +-1 s
    ball_window_s: float = 0.25  # R-BALL-WINDOW: +-0.25 s around a live-play ball jump or limit violation
    ball_restart_s: float = 0.25  # R-BALL-WINDOW: 0.25 s after each dead->live restart

    # --- Frozen tracks and gaps (R-FROZEN, R-GAP; sections 5.1, 6.3) ---
    frozen_min_s: float = 1.0  # identical position repeated for >= 1 s
    gap_resume_max_m: float = 6.0  # estimate only if the player resumes < 6 m from the last valid position
    gap_spline_max_s: float = 0.5
    gap_kalman_max_s: float = 2.0
    gap_context_s: float = 1.0  # measured samples used on each side of a gap by the spline / Kalman smoother
    kalman_meas_sigma_m: float = 0.1
    kalman_jerk_psd: float = 50.0  # (m/s^3)^2 * s, white-jerk process noise of the constant-acceleration model

    # --- Tracking-area edges and parking values (section 5.2) ---
    edge_min_samples: int = 25  # R-EDGE: ~1 s at 25 Hz, exactly the same coordinate beyond a line
    edge_value_decimals: int = 4  # coordinates are compared after rounding to this many decimals
    parking_min_samples: int = 25  # R-PARKING: frozen samples at the value, all runs together (confirmed in v4.2)
    parking_min_tracks: int = 3  # R-PARKING (v4.1): different players frozen at the value (was 2)

    # --- Substitutions and re-entry (R-SUB-INHERIT, R-EXIT-REENTRY) ---
    sub_jump_search_s: float = 60.0
    sub_inherit_max_m: float = 0.5  # incoming track starts < 0.5 m from the outgoing player's last position -> inheritance
    sub_inherit_max_frame_gap: int = 1  # ... at most 1 frame later
    sub_stabilisation_s: float = 2.0  # with a re-acquisition jump: invalid until the jump + 2 s
    sub_no_jump_invalid_s: float = 1.0  # without a jump (or without inheritance): invalid for the first 1 s only
    reentry_stabilisation_s: float = 2.0

    # --- Swaps, overlaps, crowding, jump context (R-SWAP, R-SWAP-RESTORE, R-OVERLAP-CROWD, R-JUMP-CONTEXT) ---
    swap_distance_m: float = 1.0
    swap_window_s: float = 1.0
    swap_pair_max_s: float = 120.0  # a swap and its correction must be this close in time to swap labels back
    overlap_distance_m: float = 0.3
    overlap_min_s: float = 0.2
    crowding_radius_m: float = 3.0
    crowding_min_players: int = 3
    jump_context_s: float = 2.0  # R-JUMP-CONTEXT: provider events within +-2 s of an unknown jump are recorded

    # --- Sampling (R-RESAMPLE) ---
    sampling_tolerance_frac: float = 0.05

    # --- Time alignment (section 9.1-9.3 step 1) ---
    kickoff_centre_radius_m: float = 2.0
    anchor_max_offset_s: float = 30.0  # an anchor event is paired with a dead->live transition at most this far away
    event_search_min_s: float = 5.0
    event_search_mad_factor: float = 4.0
    event_search_max_s: float = 30.0
    kickoff_search_window_s: float = 5.0  # kickoff frame search around the KickOff event (profile)
    transform_lag_search_s: float = 3.0  # global lag searched when fitting the event -> tracking transform
    transform_event_types: tuple[str, ...] = ("Play", "ShotAtGoal", "KickOff", "ThrowIn", "FreeKick", "CornerKick", "GoalKick")
    restart_event_types: tuple[str, ...] = ("KickOff", "ThrowIn", "CornerKick", "FreeKick", "GoalKick", "Penalty")
    # R-LATE-ANNOTATION (v4.2): when more than this share of the samples in the search window is dead ball, the search
    # for a kick extends backwards to the previous live phase, up to late_annotation_max_s before the corrected time.
    late_annotation_dead_share: float = 0.5
    late_annotation_max_s: float = 20.0

    # --- Ball touches (section 9.5) ---
    touch_acc_min_ms2: float = 8.0  # R-TOUCH: smallest |a| peak that counts as a ground touch
    touch_reach_m: float = 2.0  # the acting player is within this distance of the ball around the peak
    touch_peak_tolerance_s: float = 0.2  # +- around the peak for the reach test (the filter spreads the impulse)
    # R-TOUCH-NEAREST (v4.2): the kicker's closest approach to the ball within +-0.2 s of the peak (the R-TOUCH tolerance)
    # is the smallest of all players or within this margin of it: 0.3 m for ground touches, 0.5 m for aerial touches
    # (tracking measures the torso, not the head: in aerial duels the defender's torso is often 0.3-0.4 m nearer).
    touch_nearest_margin_m: float = 0.3
    touch_nearest_margin_aerial_m: float = 0.5
    ground_touch_max_height_m: float = 1.5  # R-TOUCH (v4.2): a ground touch needs the ball at or below 1.5 m at the peak
    kick_away_s: float = 0.5  # after a kick the ball must move away from the player within this time ...
    kick_away_min_m: float = 1.0  # ... by at least this distance
    kick_min_separation_s: float = 0.2  # R-TOUCH / R-ALIGN: touches closer than this are one touch
    shot_touch_min_separation_s: float = 0.08  # R-SHOT-SPLIT: after a shot, touches 0.08 s (2 frames) apart are separate
    soft_kick_away_m: float = 1.0  # R-SHOT-SOFT: the ball-shooter distance grows by more than 1 m ...
    soft_kick_window_s: float = 0.3  # ... within 0.3 s (8 samples at 25 Hz)
    aerial_min_height_m: float = 1.5  # R-AERIAL: aerial touch for passes and clearances
    header_shot_min_height_m: float = 1.6  # R-SHOT-HEADER: aerial touch for shots
    aerial_dir_change_min_deg: float = 10.0  # R-AERIAL (v4.1): was 20 deg
    aerial_acc_min_ms2: float = 2.0  # R-AERIAL (v4.1): softer impulse than a kick; was 4 m/s2
    aerial_min_speed_ms: float = 1.0  # R-AERIAL: both horizontal speeds around the touch (confirmed in v4.2)
    duel_reach_m: float = 3.0  # R-DUEL: both players within this distance of the ball at the located frame
    # R-DUEL / R-ALIGN (v4.2): an aerial duel (Type air) whose WinnerAction is one of these, won by the player of the next
    # shot, may share the shot's frame (the order constraint is relaxed for that pair only).
    duel_shot_winner_actions: tuple[str, ...] = ("shot",)
    duel_aerial_type: str = "air"
    sync_max_candidates: int = 8  # R-ALIGN: candidate frames kept per event
    sync_anchor_weight: float = 2.0  # R-ALIGN: located restarts and goal shots are fixed anchors (an ordinary match scores <= 1)

    # --- Goals (R-GOAL, section 9.6) ---
    goal_width_m: float = 7.32  # IFAB Law 1 (inside of the posts)
    crossbar_height_m: float = 2.44  # IFAB Law 1
    ball_radius_m: float = 0.11  # IFAB Law 2 (circumference 68-70 cm)
    goal_dead_within_s: float = 5.0  # R-GOAL: the ball goes dead within 5 s of a goal crossing (confirmed in v4.2)
    # R-GOAL entry rule (v4.2): after the ball has gone wholly over a goal line outside the goal frame, crossings are
    # ignored until it has been back on the pitch for at least this long (5 samples at 25 Hz).
    goal_reentry_min_s: float = 0.2
    # R-GOAL-FREEZE (v4.2): the last live velocity is extrapolated for at most this long (2 frames at 25 Hz) when the
    # next sample is dead and frozen, only if no player is within goal_freeze_player_min_m of the ball.
    goal_freeze_extrapolate_s: float = 0.08
    goal_freeze_player_min_m: float = 1.0
    # R-GOAL (v4.1): a crossing with no restart before the end of the period is a goal confirmed by the end of the period
    # only when none of these events comes between the crossing (minus the search window) and the FinalWhistle.
    goal_end_blocking_events: tuple[str, ...] = ("VideoAssistantAction", "GoalDisallowed", "Foul", "Offside")
    final_whistle_event: str = "FinalWhistle"
    goal_disallowed_event: str = "GoalDisallowed"  # R-GOAL (v4.2): its Reason qualifier is the disallowed_reason

    # --- Shots (section 9.7) ---
    next_touch_max_s: float = 10.0  # next touch / free flight searched up to this long after the kick
    shot_wide_margin_m: float = 6.0  # R-SHOT-DIRECTION: the fitted line crosses the goal line within 6 m beyond a post ...
    # ... or within 8 m (v4.1) for kicks more than 16.5 m from the goal line and for shots above the power threshold.
    shot_wide_margin_long_m: float = 8.0
    shot_long_range_m: float = 16.5  # distance of the kick from the goal line beyond which the 8 m margin applies (IFAB box depth)
    shot_flight_min_samples: int = 3  # R-SHOT-DIRECTION: live samples needed for the fit (confirmed in v4.2)
    shot_teammate_line_m: float = 2.0  # R-SHOT-NOT-PASS: team-mate ahead of the ball within 2 m of the fitted line
    shot_block_at_kick_m: float = 1.0  # R-SHOT-OUTCOME (v4.2): away-from-goal flight with an opponent this close at the kick = blocked
    shot_power_window_s: float = 0.5  # R-SHOT-POWER: peak valid ball speed in the 0.5 s after the kick
    # R-SHOT-POWER: used only when calibration/ball.json has no pass_speed_ms.p99. Set on 2026-10-25 (N-4) to the DFL pass p99
    # of the v4 calibration: 26.3486 m/s = 94.86 km/h (n = 4575 located open-play passes, six matches). Was 95.0 (v3 guess).
    pass_speed_p99_kmh: float = 94.86
    shot_block_max_s: float = 2.0  # R-SHOT-OUTCOME: blocked = opposing outfield touch within 2 s of the kick
    # R-WOODWORK (v4.2): candidate = an |a| peak in live play with the ball's path (segments between consecutive samples)
    # within woodwork_distance_m of a post or the crossbar. It is woodwork when (i) the path is within
    # woodwork_close_m of the frame and every player is at least woodwork_player_min_m away, or (ii) the ball is closer to
    # the frame than to any player by more than woodwork_margin_m. Above player_reach_height_m the player distance is
    # 3-D, from a point at that height above the player's position (a standing player's reach).
    woodwork_distance_m: float = 0.5
    woodwork_close_m: float = 0.3
    woodwork_player_min_m: float = 1.0
    woodwork_margin_m: float = 0.5
    player_reach_height_m: float = 2.3

    # --- Passes and clearances (sections 9.8, 9.9) ---
    pass_open_space_min_m: float = 5.0  # R-PASS-OUTCOME: untouched ball that travelled at least this far = "open space"
    clearance_attribute: str = "DefensiveClearance"  # DFL OtherBallAction flag for clearances (provider mapping)
    clearance_min_conditions: int = 4  # R-CLEARANCE: at least 4 of the 7 conditions
    clear_short_possession_s: float = 1.0  # C1 (confirmed in v4.2)
    clear_power_min_kmh: float = 72.0  # C2 (confirmed in v4.2)
    clear_high_ball_m: float = 3.0  # C2 (confirmed in v4.2)
    clear_free_radius_m: float = 5.0  # C3 (confirmed in v4.2)
    clear_opponent_radius_m: float = 4.0  # C4 (v4.1): at least clear_min_opponents opponents within 4 m of the ball at the kick
    clear_min_opponents: int = 1  # C4 (v4.1)
    clear_box_min_opponents: int = 3  # C5 (confirmed in v4.2)
    clear_after_s: float = 3.0  # C7 (confirmed in v4.2)
    clear_defender_groups: tuple[str, ...] = ("CB", "FB")  # C5: role groups of section 16

    # --- Event location (step 6): zones in attacking-normalised coordinates ---
    penalty_area_depth_m: float = 16.5  # IFAB Law 1
    penalty_area_width_m: float = 40.32  # IFAB Law 1

    # --- Metrics (section 10) ---
    speed_zones_kmh: tuple[float, ...] = (7.0, 15.0, 20.0, 25.0)  # R-ZONES: FIFA zones [0,7), [7,15), [15,20), [20,25), [25, inf)
    sprint_min_kmh: float = 25.0  # R-SPRINT: at least 25 km/h (exactly 25.0 is a sprint, METRICS.md decision 21) ...
    sprint_min_s: float = 1.0  # ... for at least 1 s (decided in E-3, implemented in E-4)
    sprint_merge_gap_s: float = 1.0  # R-SPRINT: two sprints less than 1 s apart count as one (METRICS.md section 19)
    precision_exact_below: float = 0.01  # R-DIST-PRECISION
    precision_tenth_below: float = 0.05
    # Match metrics of Materials/METRICS.md (decisions of 25 October 2026, implemented in E-4)
    chance_big_min_xg: float = 0.30  # section 9: xG >= 0.30 "big chance" ...
    chance_decent_min_xg: float = 0.10  # ... 0.10-0.30 "decent chance", below "half chance"
    long_ball_min_m: float = 32.0  # section 11: a successful pass of 32 m or more (Opta's 35 yards)
    peak_window_s: float = 300.0  # section 4: rolling five-minute window ...
    peak_step_s: float = 1.0  # ... with a step of 1 s (tracking clock)
    circulation_exclude_after_s: float = 2.0  # section 6: samples in the 2 s after each shot and clearance left out
    momentum_window_s: float = 300.0  # section 13: the last 5 minutes of match time, within the current period
    momentum_tilt_weight: float = 0.5  # section 13: territory weight; danger (xG) gets 1 - 0.5
    recovery_check_s: float = 3.0  # section 21: possession column checked within 3 s before and after a recovery (E-4)
    counter_attack_max_s: float = 15.0  # section 25: a shot within 15 s of a recovery in the team's own half
    offside_band_m: float = 2.0  # section 26: inside +-2 m of the line the check is "too close to call with our data"
    offside_pass_lookback_s: float = 15.0  # section 26: the pass is the attacking team's last one within 15 s (E-4)
    zone_map_grid_m: float = 1.0  # section 15: time on a 1 m grid ...
    zone_map_sigma_m: float = 3.0  # ... smoothed with a Gaussian of sigma 3 m ...
    zone_map_cell_m: tuple[float, float] = (5.0, 4.0)  # ... shown in 5 x 4 m cells (21 x 17 on 105 x 68 m, E-4)
    ppda_zone_share: float = 0.6  # section 13: the pressing zone is the 60 % of the pitch nearest the opponent's goal

    highlight_after_s: float = 3.0  # R-HIGHLIGHT: a clip runs this long after the anchor outcome (provisional, not in section 15)

    random_seed: int = 0  # no stochastic step; kept for future use

    # Derived values ---------------------------------------------------------
    @property
    def player_speed_max_ms(self) -> float:
        return self.player_speed_max_kmh / 3.6

    @property
    def ball_speed_max_ms(self) -> float:
        return self.ball_speed_max_kmh / 3.6

    def savgol_window(self, fs_hz: float) -> int:
        """R-SG-SPEED: odd(round(fs * 0.5)); 13 samples at 25 Hz; never shorter than polyorder + 1."""
        return odd_window(fs_hz, self.savgol_window_fraction_of_fs, self.savgol_polyorder)

    def acc_window(self, fs_hz: float) -> int:
        """R-SG-ACC: odd(round(fs * f_a)); 13, 19 or 25 samples at 25 Hz for f_a = 0.5, 0.75, 1.0."""
        return odd_window(fs_hz, self.acc_window_fraction_of_fs, self.savgol_polyorder)

    def frames(self, seconds: float, fs_hz: float) -> int:
        return int(round(seconds * fs_hz))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["player_speed_max_ms"] = round(self.player_speed_max_ms, 4)
        d["ball_speed_max_ms"] = round(self.ball_speed_max_ms, 4)
        return d

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


DEFAULT_CONFIG = PrepConfig()
# Backwards-compatible alias for code written against the v2 pipeline.
QCConfig = PrepConfig
