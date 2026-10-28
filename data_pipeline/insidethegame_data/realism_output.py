"""Outputs of the Linesman realism report (``realism.py``): ``realism.json``, figures and ``README.md``.

Everything is in English: the README, the figure titles, the code, the comments and ``realism.json``. Everything is generated from the result dictionary of
``realism.realism_report``: nothing is written by hand, so a re-run rewrites the same files.

Figures (``figures/*.png``, one per metric group, small panels):

* metrics with many values per match: histogram of the synthetic sample (SYN-001 and replicas) over the DFL
  band (p10-p90 shaded), with the DFL distribution as an outline (from the reference CDF) and both medians;
* metrics with one value per match or per team-match: one dot per synthetic value (SYN-001 highlighted) over
  the DFL band, with the range of the DFL per-match medians as a bar;
* physical metrics: one column per role group, the synthetic player-matches as dots over the DFL band;
* mean positions: synthetic mean position per role against the DFL baseline of ``position_maps.json``.

Colours: the validated reference palette of the data-viz guidance (synthetic = blue, SYN-001 = orange, DFL =
neutral grey); traffic-light colours always come with their word (green / yellow / red), never alone.
Deterministic: fixed fonts and sizes, no randomness (the dot spread is a fixed pattern), PNG metadata without
software or date.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.offsetbox import AnnotationBbox, HPacker, TextArea  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from .calibration import dumps, exact_order_statistic_levels  # noqa: E402
from .realism import cdf_at, quantile_of, reference_cdf, reference_levels  # noqa: E402
from .realism_metrics import GROUPS, PHYS_GROUPS, RealismConfig, metric_catalogue  # noqa: E402

# --- visual tokens (light surface; static PNG) ---
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRIDC = "#e6e5e0"
BAND = "#e1e0da"  # DFL p10-p90
DFL_LINE = "#52514e"
SYN = "#2a78d6"  # synthetic (categorical slot 1)
SYN_DARK = "#184f95"  # synthetic median
SYN001 = "#eb6834"  # SYN-001 itself (categorical slot 2)
STATUS = {"green": "#0ca30c", "yellow": "#fab219", "red": "#d03b3b"}
LIGHT = {"green": "green", "yellow": "yellow", "red": "red", None: "n/a"}

GROUP_NAME = {"physical": "Physical by role", "ball_play": "Ball and play", "passes": "Passes", "shots": "Shots", "events": "Events",
              "team_shape": "Team shape"}
ROLE_SHORT = {"GK": "GK", "CB": "CB", "FB": "FB", "MF": "MF", "W": "W", "ST": "ST"}  # axis labels; the README spells the roles out
ROLE_LONG = {"GK": "goalkeepers", "CB": "centre-backs", "FB": "full-backs", "MF": "midfielders", "W": "wide players", "ST": "strikers"}
LABEL = {
    "distance_per90_m": "Distance per 90'", "zone_0_7_per90_m": "Distance 0-7 km/h per 90'", "zone_7_15_per90_m": "Distance 7-15 km/h per 90'",
    "zone_15_20_per90_m": "Distance 15-20 km/h per 90'", "zone_20_25_per90_m": "Distance 20-25 km/h per 90'",
    "zone_25_plus_per90_m": "Distance above 25 km/h per 90'", "distance_above_20kmh_per90_m": "Distance above 20 km/h per 90'",
    "top_speed_kmh": "Top speed", "sprints_per90": "Sprints per 90'", "peak_5min_m": "Peak distance over 5 minutes",
    "accelerations_per90": "Accelerations above 3 m/s² per 90'", "decelerations_per90": "Decelerations above 3 m/s² per 90'",
    "ball_in_play_share": "Ball in play", "possession_dominant_share": "Possession of the team that has more of it",
    "ball_speed_live_kmh": "Ball speed in play", "live_phase_duration_s": "Length of ball-in-play phases",
    "possession_spell_duration_s": "Length of possessions (1 s and longer)",
    "passes_per90": "Passes per team per 90'", "pass_length_m": "Pass length", "pass_speed_kmh": "Pass speed",
    "pass_success_own_third": "Completed passes from the defensive third", "pass_success_middle_third": "Completed passes from the middle third",
    "pass_success_final_third": "Completed passes from the attacking third",
    "shots_per_match": "Shots per match (both teams)", "shots_per90": "Shots per team per 90'", "shot_distance_m": "Shot distance",
    "shot_speed_kmh": "Shot speed (headers excluded)", "shot_xg": "xG per shot", "shot_share_goal": "Shots scored", "shot_share_saved": "Shots saved",
    "shot_share_blocked": "Shots blocked by a defender", "shot_share_wide": "Shots wide", "shot_share_woodwork": "Shots off the post or crossbar",
    "fouls_per90": "Fouls", "corners_per90": "Corners", "throw_ins_per90": "Throw-ins", "free_kicks_per90": "Free kicks",
    "cards_per90": "Cards", "tackles_per90": "Tackles", "recoveries_per90": "Ball recoveries", "clearances_per90": "Clearances",
    "goal_kicks_per90": "Goal kicks", "offsides_per90": "Offsides",
    "team_length_in_poss_m": "Team length in possession", "team_length_out_poss_m": "Team length out of possession",
    "team_width_in_poss_m": "Team width in possession", "team_width_out_poss_m": "Team width out of possession",
}
UNIT = {"m": "m", "km/h": "km/h", "s": "s", "share": "%", "count": "n", "xG": "xG"}
# What to change in the generator, by metric (used in the improvement list).
ADVICE = {
    "shots_per_match": "raise the shot probability per attacking move (event_model.json: shot.rate_per_possession_by_zone)",
    "shots_per90": "raise the shot probability per attacking move (event_model.json: shot.rate_per_possession_by_zone)",
    "shot_share_blocked": "add blocked shots: a defender on the path who touches the ball within 2 s of the shot",
    "shot_share_goal": "realign the shot outcomes with event_model.json (shot.outcome_shares)",
    "shot_share_saved": "realign the shot outcomes with event_model.json (shot.outcome_shares)",
    "shot_share_wide": "realign the shot outcomes with event_model.json (shot.outcome_shares)",
    "shot_share_woodwork": "realign the shot outcomes with event_model.json (shot.outcome_shares)",
    "shot_speed_kmh": "calibrate the initial shot speed on ball.json (shot_speed_ms)",
    "shot_distance_m": "move the shot locations as in event_model.json (shot.share_by_zone)",
    "shot_xg": "move the shot locations and the defenders' pressure (xG depends on distance, angle and defenders in the triangle)",
    "ball_speed_live_kmh": "calibrate the ball's speed and slowdown (ball.json: pass_speed_ms, circulation_speed_ms)",
    "pass_speed_kmh": "calibrate the initial pass speed on ball.json (pass_speed_ms)",
    "pass_length_m": "calibrate the pass length by zone (event_model.json: pass.length_m_by_zone)",
    "passes_per90": "calibrate the number of passes per move (event_model.json: possession)",
    "pass_success_own_third": "calibrate the pass success by zone (event_model.json: pass.success_share_by_zone)",
    "pass_success_middle_third": "calibrate the pass success by zone (event_model.json: pass.success_share_by_zone)",
    "pass_success_final_third": "calibrate the pass success by zone (event_model.json: pass.success_share_by_zone)",
    "team_length_in_poss_m": "review the team positions in possession (position_maps.json): compactness in length",
    "team_length_out_poss_m": "review the team positions out of possession (position_maps.json): compactness in length",
    "team_width_in_poss_m": "review the team positions in possession (position_maps.json): width",
    "team_width_out_poss_m": "review the team positions out of possession (position_maps.json): width",
    "top_speed_kmh": "review the top speeds by role (attribute_scales.json: pace)",
    "sprints_per90": "review how often players run above 25 km/h (physical_profiles.json: sprints_per90)",
    "zone_25_plus_per90_m": "review how often players run above 25 km/h (physical_profiles.json: speed_zone_per90_m)",
    "accelerations_per90": "review the changes of pace: how many sharp starts per 90'",
    "decelerations_per90": "review the braking: how many sharp decelerations per 90'",
    "live_phase_duration_s": "review how often play stops (fouls, throw-ins, balls out)",
    "possession_spell_duration_s": "review the length of possessions (passes per move, tackles won)",
    "ball_in_play_share": "review the dead time at restarts",
    "possession_dominant_share": "review the possession imbalance between the teams",
}
ADVICE_DEFAULT_PHYS = "review the running rhythm by role (physical_profiles.json)"
ADVICE_DEFAULT_EVENT = "review the event's rate per 90' (event_model.json: per90_per_team)"
KNOWN_FIRST_CHECK = {"shots_per_match": "already known from the first realism check: too few shots",
                     "shots_per90": "already known from the first realism check: too few shots",
                     "shot_share_blocked": "already known from the first realism check: no blocked shots"}
ITEM_TITLE = {"shots_per_match": "Number of shots"}  # items that merge several metrics (realism.SAME_ISSUE)

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                     "savefig.facecolor": SURFACE, "svg.hashsalt": "linesman"})


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def scale_of(unit: str) -> float:
    return 100.0 if unit == "share" else 1.0


def fmt(v, unit: str) -> str:
    if v is None or not np.isfinite(v):
        return "n/a"
    if unit == "share":
        return f"{100 * v:.1f} %"
    if unit == "xG":
        return f"{v:.3f}"
    if unit == "m" and abs(v) >= 100:
        return f"{v:,.0f} m".replace(",", " ")
    if unit in ("m", "km/h", "s"):
        return f"{v:.1f} {unit}"
    return f"{v:.1f}"


def fmt_range(rng, unit: str) -> str:
    if not rng:
        return "n/a"
    return f"{fmt(rng[0], unit)} – {fmt(rng[1], unit)}"


def label(m: dict) -> str:
    return LABEL.get(m["name"], m["name"])


def direction(m: dict, key: str = "synthetic") -> str:
    s = m[key]
    pos, rng, med = s.get("median_position"), m["dfl"]["match_median_range"], s.get("median")
    if med is None or rng is None:
        return ""
    if pos != "inside":
        return "synthetic higher than real" if med > rng[1] else "synthetic lower than real"
    if s.get("direction") == "higher":
        return "different distribution shape, synthetic values higher"
    if s.get("direction") == "lower":
        return "different distribution shape, synthetic values lower"
    return "different distribution shape"


def _levels(levels_pct) -> str:
    """'p2.5, p5, p10, p15, ..., p90, p95, p97.5' (decimal point, as everywhere in the report)."""
    names = [f"p{p:g}" for p in levels_pct]
    return ", ".join(names[:4] + ["..."] + names[-3:]) if len(names) > 8 else ", ".join(names)


def _exact_quantiles(ref: dict, metrics: dict) -> str:
    """'in the current reference 462 quantiles of 2 247, in 62 metrics of 107': stored quantiles at a whole-number position."""
    levels = ref["quantile_levels_pct"]
    per_metric = [len(exact_order_statistic_levels(m["dfl"]["n"], levels)) for m in metrics.values() if m["dfl"].get("n")]
    total = len(levels) * len(per_metric)
    def num(x: int) -> str:
        return f"{x:,}".replace(",", " ")
    return (f"in the current reference {num(sum(per_metric))} quantiles of {num(total)}, in {sum(1 for c in per_metric if c)} metrics "
            f"of {len(metrics)}")


def lights(counts: dict) -> str:
    """'13 green, 1 yellow, 76 red'."""
    return ", ".join(f"{counts[k]} {k}" for k in ("green", "yellow", "red"))


def light_md(light: str | None) -> str:
    w = LIGHT[light]
    return w if light in (None, "green") else f"**{w}**"


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _badge(ax, light: str | None, xy, xycoords, box_alignment=(1.0, 1.0)) -> None:
    """Traffic-light badge: a coloured dot followed by the word (never colour alone)."""
    parts = [TextArea("●", textprops={"color": STATUS.get(light, INK2), "fontsize": 11}),
             TextArea(LIGHT[light], textprops={"color": INK, "fontsize": 8.5, "fontweight": "bold"})]
    box = HPacker(children=parts, align="center", pad=0, sep=2)
    ax.add_artist(AnnotationBbox(box, xy, xycoords=xycoords, box_alignment=box_alignment, frameon=False, annotation_clip=False))


def _style(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=3)


def _spread(n: int, width: float) -> np.ndarray:
    """Fixed horizontal spread of n dots (a repeating pattern, no randomness)."""
    pattern = np.array([0, 3, -3, 1.5, -1.5, 4.5, -4.5, 0.75, -0.75, 2.25, -2.25, 3.75, -3.75])
    return pattern[np.arange(n) % len(pattern)] / 4.5 * width


def _hist_panel(ax, m: dict, ref: dict, pooled: np.ndarray, levels: list[float]) -> None:
    sc = scale_of(m["unit"])
    q = np.asarray(ref["quantiles"], float)
    knots = reference_cdf(q, levels)
    s = np.asarray(pooled, float)
    lo_candidates = [quantile_of(list(q), 0.01, levels)]  # clamped to the lowest stored quantile (p2.5)
    hi_candidates = [quantile_of(list(q), 0.99, levels)]  # ... and to the highest (p97.5)
    if len(s):
        lo_candidates.append(float(np.percentile(s, 1)))
        hi_candidates.append(float(np.percentile(s, 99)))
    lo, hi = min(lo_candidates), max(hi_candidates)
    if hi <= lo:
        lo, hi = lo - 0.5, hi + 0.5
    edges = np.linspace(lo, hi, 31)
    w = np.diff(edges)
    ax.axvspan(m["dfl"]["p10"] * sc, m["dfl"]["p90"] * sc, color=BAND, zorder=0, lw=0)
    if len(s):
        cnt, _ = np.histogram(s, bins=edges)
        dens = cnt / (len(s) * w)
        ax.bar(edges[:-1] * sc, dens / sc, width=w * sc * 0.9, align="edge", color=SYN, zorder=2, lw=0)
    # The DFL outline shows the reference between its lowest and highest stored quantiles only: the jumps of the KS CDF at
    # p2.5 and p97.5 (the 2.5 % tails, whose shape is not stored) are not drawn as spikes.
    mass = np.diff(np.clip(cdf_at(edges, knots), levels[0], levels[-1]))
    dref = mass / w
    ax.step(edges * sc, np.r_[dref, dref[-1]] / sc, where="post", color=DFL_LINE, lw=1.8, zorder=3)
    ax.axvline(m["dfl"]["median"] * sc, color=DFL_LINE, lw=1.4, ls=(0, (4, 2)), zorder=4)
    if m["synthetic"]["median"] is not None:
        ax.axvline(m["synthetic"]["median"] * sc, color=SYN_DARK, lw=2.2, zorder=5)
    ax.set_yticks([])
    ax.set_xlabel(UNIT.get(m["unit"], m["unit"]))
    ax.set_xlim(lo * sc, hi * sc)
    ax.set_ylim(0, ax.get_ylim()[1] * 1.32)
    d = m["synthetic"]["ks_d"]
    d1 = m["syn_match"]["ks_d"]
    txt = (f"D = {d:.2f} (SYN-001 alone: {d1:.2f})" if d is not None and d1 is not None else "D n/a")
    txt += f"\nsynthetic median {fmt(m['synthetic']['median'], m['unit'])}\nreal {fmt_range(m['dfl']['match_median_range'], m['unit'])}"
    ax.text(0.01, 0.98, txt, transform=ax.transAxes, va="top", ha="left", fontsize=7.5, color=INK2, zorder=6)
    small = " †" if m.get("small_sample") else ""
    ax.set_title(label(m) + small, loc="left", color=INK)
    _badge(ax, m["light"], (0.995, 0.995), "axes fraction")


def _strip_panel(ax, m: dict, ref: dict, values_by_match: list[tuple[str, np.ndarray]], match_id: str) -> None:
    sc = scale_of(m["unit"])
    rng = m["dfl"]["match_median_range"]
    p10, p90 = m["dfl"]["p10"], m["dfl"]["p90"]
    allv = [v for _, vals in values_by_match for v in vals] + [p10, p90] + (list(rng) if rng else [])
    lo, hi = min(allv), max(allv)
    pad = 0.08 * (hi - lo) if hi > lo else max(abs(hi), 1.0) * 0.1
    ax.axvspan(p10 * sc, p90 * sc, color=BAND, zorder=0, lw=0)
    if rng:
        ax.hlines(0.74, rng[0] * sc, rng[1] * sc, color=DFL_LINE, lw=6, zorder=2)
        ax.vlines([rng[0] * sc, rng[1] * sc], 0.66, 0.82, color=DFL_LINE, lw=1.4, zorder=2)
    k = 0
    for mid, vals in values_by_match:
        for j, v in enumerate(vals):
            y = 0.32 + (0.07 if j % 2 else -0.07) * (len(vals) > 1) + 0.025 * ((k % 3) - 1)
            ax.scatter([v * sc], [y], s=34, color=SYN001 if mid == match_id else SYN, edgecolor=SURFACE, linewidth=0.8, zorder=4)
        k += 1
    if m["synthetic"]["median"] is not None:
        ax.vlines(m["synthetic"]["median"] * sc, 0.12, 0.54, color=SYN_DARK, lw=2.2, zorder=5)
    ax.set_xlim((lo - pad) * sc, (hi + pad) * sc)
    ax.set_ylim(0, 1.25)
    ax.set_yticks([])
    ax.set_xlabel(UNIT.get(m["unit"], m["unit"]))
    txt = f"synthetic median {fmt(m['synthetic']['median'], m['unit'])} · real {fmt_range(rng, m['unit'])}"
    ax.text(0.01, 0.98, txt, transform=ax.transAxes, va="top", ha="left", fontsize=7.5, color=INK2, zorder=6)
    ax.set_title(label(m), loc="left", color=INK)
    _badge(ax, m["light"], (0.995, 0.995), "axes fraction")


def _legend(fig, physical: bool = False, strip: bool = True, hist: bool = True) -> None:
    h = [Patch(color=BAND, label="Real DFL: p10 to p90")]
    if hist or physical:
        h.append(Line2D([], [], color=DFL_LINE, lw=1.8 if hist else 2, ls=(0, (4, 2)) if hist and not physical else "-",
                        label="Real DFL: median" + (" (dashed line) and distribution" if hist and not physical else "")))
    if strip:
        h.append(Line2D([], [], color=DFL_LINE, lw=6, label="Real DFL: medians of the 6 matches (min to max)"))
    if hist and not physical:
        h.append(Patch(color=SYN, label="Synthetic: histogram (SYN-001 + replicas)"))
    if strip or physical:
        dot = "Synthetic: one player in one match (replicas)" if physical else "Synthetic: one match or one team (replicas)"
        h.append(Line2D([], [], marker="o", ls="", color=SYN, markeredgecolor=SURFACE, markersize=7, label=dot))
        h.append(Line2D([], [], marker="o", ls="", color=SYN001, markeredgecolor=SURFACE, markersize=7, label="SYN-001"))
    h.append(Line2D([], [], color=SYN_DARK, lw=2.2, label="Synthetic: median"))
    fig.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, 0.955), ncol=min(4, len(h)), frameon=False, fontsize=8.5)


def _save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100, metadata={"Software": None})
    plt.close(fig)


def figure_group(group: str, result: dict, reference: dict, per_match: dict, match_id: str, path: Path) -> None:
    keys = [k for k, m in result["metrics"].items() if m["group"] == group]
    ncols = 2 if len(keys) <= 4 else 3
    nrows = int(np.ceil(len(keys) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.4 * ncols, 3.1 * nrows + 1.3), squeeze=False)
    has_hist = has_strip = False
    for ax, key in zip(axes.ravel(), keys):
        m, ref = result["metrics"][key], reference["metrics"][key]
        _style(ax)
        if not ref.get("quantiles"):
            ax.set_title(label(m), loc="left", color=INK)
            ax.text(0.5, 0.5, "no real data for this metric", transform=ax.transAxes, ha="center", va="center", color=INK2)
            ax.set_xticks([])
            ax.set_yticks([])
            continue
        if m["comparison"] == "ks_and_range":
            has_hist = True
            pooled = np.concatenate([per_match[mid][key] for mid in per_match])
            _hist_panel(ax, m, ref, pooled, reference_levels(reference))
        else:
            has_strip = True
            _strip_panel(ax, m, ref, [(mid, per_match[mid][key]) for mid in per_match], match_id)
    for ax in axes.ravel()[len(keys):]:
        ax.set_visible(False)
    n = len(per_match) - 1
    fig.suptitle(f"{GROUP_NAME[group]}: {match_id} and {n} replicas against 6 real DFL matches", x=0.01, ha="left", fontsize=13, fontweight="bold")
    _legend(fig, strip=has_strip, hist=has_hist)
    fig.tight_layout(rect=(0, 0, 1, 0.9), h_pad=1.6, w_pad=1.4)
    _save(fig, path)


def figure_physical(result: dict, reference: dict, per_match: dict, match_id: str, path: Path) -> None:
    names = []
    for m in result["metrics"].values():
        if m["group"] == "physical" and m["name"] not in names:
            names.append(m["name"])
    ncols = 4
    nrows = int(np.ceil(len(names) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.6 * ncols, 3.6 * nrows + 1.3), squeeze=False)
    for ax, name in zip(axes.ravel(), names):
        _style(ax)
        unit = None
        for i, g in enumerate(PHYS_GROUPS):
            key = f"physical.{name}.{g}"
            m = result["metrics"][key]
            unit = m["unit"]
            if m["dfl"]["p10"] is not None:
                ax.add_patch(Rectangle((i - 0.38, m["dfl"]["p10"]), 0.76, m["dfl"]["p90"] - m["dfl"]["p10"], color=BAND, lw=0, zorder=1))
                ax.hlines(m["dfl"]["median"], i - 0.38, i + 0.38, color=DFL_LINE, lw=2, zorder=2)
            k = 0
            for mid in per_match:
                vals = np.sort(per_match[mid][key])
                xs = i + _spread(len(vals), 0.3) + 0.02 * ((k % 5) - 2)
                ax.scatter(xs, vals, s=12, color=SYN001 if mid == match_id else SYN, edgecolor=SURFACE, linewidth=0.5, zorder=3)
                k += 1
            if m["synthetic"]["median"] is not None:
                ax.hlines(m["synthetic"]["median"], i - 0.3, i + 0.3, color=SYN_DARK, lw=2.6, zorder=4)
            _badge(ax, m["light"], (i, -0.2), ("data", "axes fraction"), box_alignment=(0.5, 0.5))
        ax.set_xticks(range(len(PHYS_GROUPS)))
        ax.set_xticklabels([ROLE_SHORT[g] for g in PHYS_GROUPS], fontsize=8)
        ax.set_xlim(-0.6, len(PHYS_GROUPS) - 0.4)
        ax.set_title(LABEL.get(name, name), loc="left", color=INK)
        ax.set_ylabel(UNIT.get(unit, unit))
        ax.grid(axis="y", color=GRIDC, lw=0.6, zorder=0)
        ax.set_axisbelow(True)
    for ax in axes.ravel()[len(names):]:
        ax.set_visible(False)
    n = len(per_match) - 1
    fig.suptitle(f"Physical by role: {match_id} and {n} replicas against 6 real DFL matches (one dot = one player in one match)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    _legend(fig, physical=True, strip=False, hist=False)
    fig.tight_layout(rect=(0, 0, 1, 0.92), h_pad=3.2, w_pad=1.2)
    _save(fig, path)


def _pitch(ax, L: float = 105.0, W: float = 68.0) -> None:
    c = dict(color=INK2, lw=1.0, zorder=1)
    ax.plot([-L / 2, L / 2, L / 2, -L / 2, -L / 2], [-W / 2, -W / 2, W / 2, W / 2, -W / 2], **c)
    ax.plot([0, 0], [-W / 2, W / 2], **c)
    t = np.linspace(0, 2 * np.pi, 100)
    ax.plot(9.15 * np.cos(t), 9.15 * np.sin(t), **c)
    for s in (-1, 1):
        ax.plot([s * L / 2, s * (L / 2 - 16.5), s * (L / 2 - 16.5), s * L / 2], [-20.16, -20.16, 20.16, 20.16], **c)
        ax.plot([s * L / 2, s * (L / 2 - 5.5), s * (L / 2 - 5.5), s * L / 2], [-9.16, -9.16, 9.16, 9.16], **c)
    ax.set_xlim(-L / 2 - 3, L / 2 + 3)
    ax.set_ylim(-W / 2 - 3, W / 2 + 7)
    ax.set_aspect("equal")
    ax.axis("off")


def figure_positions(result: dict, match_id: str, path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.4))
    for ax, (state, title) in zip(axes, (("in_possession", "In possession"), ("out_of_possession", "Out of possession"))):
        _pitch(ax)
        for role, row in sorted(result["mean_positions"].items()):
            r = row[state]
            syn, base = r["synthetic_mean_xy_m"], r["dfl_baseline_xy_m"]
            if syn is None:
                continue
            if base is not None:
                ax.plot([base[0], syn[0]], [base[1], syn[1]], color=INK2, lw=0.9, zorder=2)
                ax.scatter([base[0]], [base[1]], s=48, facecolor=SURFACE, edgecolor=DFL_LINE, linewidth=1.6, zorder=3)
            ax.scatter([syn[0]], [syn[1]], s=48, color=SYN, edgecolor=SURFACE, linewidth=1.0, zorder=4)
            ax.annotate(role, (syn[0], syn[1]), xytext=(5, 4), textcoords="offset points", fontsize=8, color=INK, zorder=5)
        ax.annotate("", xy=(48, 37.5), xytext=(28, 37.5), arrowprops=dict(arrowstyle="->", color=INK2, lw=1.2))
        ax.text(26, 37.5, "the team attacks to the right", ha="right", va="center", fontsize=8, color=INK2)
        ax.set_title(title, loc="left", color=INK, fontsize=11)
    fig.suptitle(f"Mean positions by role: {match_id} and replicas (filled) against real DFL (hollow, position_maps.json)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    h = [Line2D([], [], marker="o", ls="", markerfacecolor=SURFACE, markeredgecolor=DFL_LINE, markersize=8, label="Real DFL: mean position of the role"),
         Line2D([], [], marker="o", ls="", color=SYN, markersize=8, label="Synthetic: mean position of the role"),
         Line2D([], [], color=INK2, lw=0.9, label="Gap between the two")]
    fig.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, 0.93), ncol=3, frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    _save(fig, path)


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

def _names_list(items: list[str], limit: int = 8) -> str:
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def _group_sentences(group: str, metrics: dict) -> list[str]:
    ms = [m for m in metrics.values() if m["group"] == group]
    if group == "physical":
        names = []
        for m in ms:
            if m["name"] not in names:
                names.append(m["name"])
        counts = {lab: sum(m["light"] == lab for m in ms) for lab in ("green", "yellow", "red")}
        noise = sum(1 for m in ms if m["light"] in ("red", "yellow") and m["synthetic"]["median_position"] == "inside"
                    and m["synthetic"]["ks_d"] is not None and m["synthetic"]["ks_d"] <= (m["synthetic"]["ks_d_noise95"] or 0))
        out = [f"Of {len(ms)} metric × role combinations: {lights(counts)}; "
               f"{noise} yellow or red ones have the median inside the real range and a D within chance."]
        far = []
        for n in names:
            rows = [m for m in ms if m["name"] == n and m["synthetic"]["median_position"] == "far_outside"]
            up = [ROLE_LONG[m["role_group"]] for m in rows if m["synthetic"]["median"] > m["dfl"]["match_median_range"][1]]
            down = [ROLE_LONG[m["role_group"]] for m in rows if m["synthetic"]["median"] < m["dfl"]["match_median_range"][0]]
            if len(up) + len(down) >= 3:
                parts = ([f"higher for {', '.join(up)}"] if up else []) + ([f"lower for {', '.join(down)}"] if down else [])
                far.append(f"{LABEL[n].lower()} ({'; '.join(parts)})")
        if far:
            out.append("Furthest from real, with the synthetic median far from the range in at least 3 roles: " + "; ".join(far) + ".")
        close = [LABEL[n].lower() for n in names
                 if sum(m["synthetic"]["median_position"] == "inside" for m in ms if m["name"] == n) >= 4]
        if close:
            out.append(f"Close to real in the median (inside the range in at least 4 roles of 6, though the distribution shape may differ): "
                       f"{', '.join(close)}.")
        return out
    green = [label(m) for m in ms if m["light"] == "green"]
    bad = [f"{label(m)} ({LIGHT[m['light']]}, {direction(m)})" for m in ms if m["light"] in ("red", "yellow")]
    out = []
    if green:
        out.append(f"Realistic: {_names_list(green)}.")
    out.append(f"To review: {'; '.join(bad)}." if bad else "No yellow or red metric in this group.")
    return out


def _table_group(group: str, metrics: dict) -> list[str]:
    lines = ["| Metric | Light | D (all) | D by chance (95 %) | D (SYN-001 alone) | Synthetic median | SYN-001 median | "
             "Real: medians per match (min – max) | n synthetic / real |",
             "|---|---|---|---|---|---|---|---|---|"]
    for m in metrics.values():
        if m["group"] != group:
            continue
        u = m["unit"]
        d = "–" if m["synthetic"]["ks_d"] is None else f"{m['synthetic']['ks_d']:.2f}"
        dn = "–" if m["synthetic"].get("ks_d_noise95") is None else f"{m['synthetic']['ks_d_noise95']:.2f}"
        d1 = "–" if m["syn_match"]["ks_d"] is None else f"{m['syn_match']['ks_d']:.2f}"
        small = " †" if m.get("small_sample") else ""
        n_txt = f"{m['synthetic']['n']:,} / {m['dfl']['n']:,}".replace(",", " ")
        lines.append(f"| {label(m)}{small} | {light_md(m['light'])} | {d} | {dn} | {d1} | {fmt(m['synthetic']['median'], u)} | "
                     f"{fmt(m['syn_match']['median'], u)} | {fmt_range(m['dfl']['match_median_range'], u)} | {n_txt} |")
    return lines


def _table_physical(metrics: dict) -> list[str]:
    names = []
    for m in metrics.values():
        if m["group"] == "physical" and m["name"] not in names:
            names.append(m["name"])
    head = "| Metric | " + " | ".join(ROLE_LONG[g].capitalize() for g in PHYS_GROUPS) + " |"
    lines = [head, "|---|" + "---|" * len(PHYS_GROUPS)]
    for n in names:
        cells = []
        for g in PHYS_GROUPS:
            m = metrics[f"physical.{n}.{g}"]
            d, dn = m["synthetic"]["ks_d"], m["synthetic"].get("ks_d_noise95")
            small = " †" if m.get("small_sample") else ""
            noise = f"; chance {dn:.2f}" if dn is not None else ""
            cells.append(f"{light_md(m['light'])} (D {d:.2f}{noise}){small}" if d is not None else light_md(m["light"]))
        lines.append(f"| {LABEL[n]} | " + " | ".join(cells) + " |")
    lines += ["", "Medians (synthetic / real, range of the medians per match):", "",
              "| Metric | " + " | ".join(ROLE_LONG[g].capitalize() for g in PHYS_GROUPS) + " |", "|---|" + "---|" * len(PHYS_GROUPS)]
    for n in names:
        cells = []
        for g in PHYS_GROUPS:
            m = metrics[f"physical.{n}.{g}"]
            u = m["unit"]
            cells.append(f"{fmt(m['synthetic']['median'], u)} / {fmt_range(m['dfl']['match_median_range'], u)}")
        lines.append(f"| {LABEL[n]} | " + " | ".join(cells) + " |")
    return lines


def _role_item(m: dict) -> str:
    """One role group of a physical improvement item: light, medians and D."""
    u = m["unit"]
    d, dn = m["synthetic"]["ks_d"], m["synthetic"].get("ks_d_noise95")
    dtxt = "" if d is None else f", D {d:.2f}"
    if d is not None and dn is not None and d <= dn and m["synthetic"]["median_position"] == "inside":
        dtxt += f" within chance {dn:.2f}"
    return (f"{ROLE_LONG[m['role_group']]} {LIGHT[m['light']]} (synthetic {fmt(m['synthetic']['median'], u)}, "
            f"real {fmt_range(m['dfl']['match_median_range'], u)}{dtxt})")


def _improvements(result: dict, match_id: str) -> list[str]:
    metrics = result["metrics"]
    items = []
    for it in result["improvements"]:
        ms = [metrics[k] for k in it["keys"]]
        name = it["name"]
        if it["group"] == "physical":
            what = ", ".join(_role_item(m) for m in ms)
            advice = ADVICE.get(name, ADVICE_DEFAULT_PHYS)
        else:
            parts = []
            for m in ms:
                u = m["unit"]
                dtxt = "" if m["synthetic"]["ks_d"] is None else f"; D = {m['synthetic']['ks_d']:.2f}"
                prefix = f"{label(m).lower()}: " if len(ms) > 1 else ""
                parts.append(f"{prefix}{LIGHT[m['light']]}, {direction(m)}: synthetic {fmt(m['synthetic']['median'], u)} ({match_id}: "
                             f"{fmt(m['syn_match']['median'], u)}) against real {fmt_range(m['dfl']['match_median_range'], u)}{dtxt}")
            what = " · ".join(parts)
            advice = ADVICE.get(name, ADVICE_DEFAULT_EVENT if it["group"] == "events" else "review the generator's model")
        known = f" ({KNOWN_FIRST_CHECK[name]})" if name in KNOWN_FIRST_CHECK else ""
        title = ITEM_TITLE.get(name, LABEL.get(name, name))
        items.append((it["visibility"], 2 if it["worst"] == "red" else 1, f"**{title}**{known} - {what}. What to do: {advice}."))
    for diag in result.get("woodwork_issues", []):
        items.append((4, 2, diag))
    items.sort(key=lambda t: (-t[0], -t[1]))
    return [f"{k}. {text}" for k, (_, _, text) in enumerate(items, 1)]


def _dfl_woodwork(result: dict) -> str:
    ct = (result["shots_diagnostic"].get("dfl_label_vs_observed_outcome") or {}).get("woodwork")
    if not ct:
        return ""
    n = sum(ct.values())
    seen = ct.get("woodwork", 0)
    other = ", ".join(f"{k} {v}" for k, v in sorted(ct.items()) if k != "woodwork")
    return (f" It happens on real data too: in the 6 DFL matches the pipeline recognises {seen} of the {n} shots labelled as woodwork "
            f"(outcomes seen: {other}).")


def _woodwork_text(result: dict, match_id: str) -> list[str]:
    """README items for woodwork hits labelled by the simulation but not recognised by the pipeline (R-WOODWORK)."""
    out = []
    for mid, shots in sorted(result["shots_diagnostic"]["woodwork_shots"].items()):
        for s in shots:
            if s.get("observed") == "woodwork":
                continue
            head = (f"**Post or crossbar not recognised by the pipeline** ({'already known from the first realism check, ' if mid == match_id else ''}"
                    f"{mid}, minute {s['minute']}): the simulation labels it as woodwork, the pipeline sees '{s.get('observed') or 'no outcome'}'.")
            if s.get("nearest_woodwork_m") is None:
                out.append(head + " The shot was not located on the trajectories." + _dfl_woodwork(result))
                continue
            part = "from the post" if s["nearest_woodwork_part"] == "post" else "from the crossbar"
            player = "" if s.get("nearest_player_m") is None else f" and the nearest player is {s['nearest_player_m']:.1f} m away"
            out.append(f"{head} In flight, the ball sample closest to the woodwork is {s['nearest_woodwork_m']:.2f} m {part} "
                       f"({s['nearest_after_kick_s']:.2f} s after the shot){player}; at {s['shot_speed_kmh']:.0f} km/h the ball travels "
                       f"{s['ball_step_per_frame_m']:.2f} m between two frames at 25 Hz. Rule `R-WOODWORK` (v4.2) measures the distance from the "
                       "trajectory segment between two samples and compares it with the players' distances, but here it does not recognise the "
                       "woodwork: to be checked on the shot (simulated trajectory or rule)." + _dfl_woodwork(result))
    return out


def readme(result: dict, match_id: str) -> str:
    meta = result["meta"]
    metrics = result["metrics"]
    s = result["summary"]
    n_rep = len(meta["synthetic_matches"]) - 1
    lt, la = s["lights"], s["lights_match_alone"]
    result = dict(result)
    result["woodwork_issues"] = _woodwork_text(result, match_id)
    L = [f"# Linesman: how realistic the synthetic match {match_id} is", ""]
    L += [f"> Generated by `{meta['generated_by']}`: do not edit by hand. Every number is in [`realism.json`](realism.json), "
          "the figures in [`figures/`](figures/).",
          "> Real data: only aggregates of the 6 DFL/Sportec matches (Bassek et al. 2025, Scientific Data 12:195, "
          "https://doi.org/10.1038/s41597-025-04505-y, CC-BY 4.0) in `calibration/realism_reference.json`; no raw, per-frame or per-person data. "
          "Synthetic data: the invented universe of Meridia.", ""]
    L += ["## In short", "",
          f"- **{s['n_metrics']} metrics**: {lights(lt)} ({match_id} and {n_rep} replicas together). {match_id} alone: {lights(la)}.",
          "- By group: " + "; ".join(f"{GROUP_NAME[g].lower()} {v['green']}/{v['yellow']}/{v['red']}" for g, v in s["by_group"].items())
          + " (green/yellow/red).",
          f"- To read with care: {len(s['red_or_yellow_within_noise'])} of the yellow or red metrics have the median inside the real range "
          "and a D no larger than chance would give with so few values (column \"D by chance\"); "
          f"{sum(k.startswith('physical.') for k in s['red_or_yellow_within_noise'])} of them are physical metrics by role, with few "
          "player-matches per side.",
          "- The first things to improve, ranked by how visible they are in a 2-minute video, are in "
          "[What to improve in the generator](#what-to-improve-in-the-generator).", ""]
    L += ["## How to read it", "",
          f"- **The same pipeline on both sides.** The 6 DFL matches and the synthetic matches ({match_id} plus {n_rep} replicas: same fixture and "
          f"same squads, no script) go through the same preparation (`qc.py`, guidelines {meta['guidelines_version'].split()[0]}) and the same "
          "metric code (`realism_metrics.py`). "
          "Only valid samples are used.",
          "- **Kolmogorov-Smirnov D**: the largest gap between the synthetic and the real cumulative distribution (rebuilt from the "
          f"{len(meta['reference']['quantile_levels_pct'])} stored quantiles, see [Technical details](#technical-details)). "
          "0 means identical distributions, 1 fully separate.",
          "- **Median rule**: where the synthetic median falls against the range of the medians of the 6 real matches (min to max).",
          f"- **Traffic light** (on the full synthetic sample, {match_id} plus replicas): **green** if D ≤ {meta['realism_config']['green_ks_max']} "
          f"and the median is inside the range; **yellow** if D is between {meta['realism_config']['green_ks_max']} and "
          f"{meta['realism_config']['yellow_ks_max']} or the median is just outside (within {100 * meta['realism_config']['range_tolerance_frac']:g} % "
          f"of the range width); **red** if D > {meta['realism_config']['yellow_ks_max']} or the median is far outside.",
          "- **Metrics with one value per match or per team** (ball in play, possession, counts per team, shot outcomes): with 6-7 values "
          "the KS test makes no sense, so only the median rule applies (D = –).",
          f"- **†** = small sample (fewer than {meta['realism_config']['small_sample_n']} values on one side): indicative result.",
          "- **D by chance (95 %)**: the D that two samples of these sizes drawn from the *same* distribution exceed only 5 times in 100 "
          "(critical value of the two-sample KS test). It is not part of the traffic light, which follows fixed thresholds: it shows whether "
          "a high D can be just noise. With 12-35 player-matches per side it is 0.3-0.5; for per-frame samples (ball speed, team shape) it is "
          "very low but optimistic, because nearby frames are not independent.",
          "- Physical values are per 90 minutes of measured time, players with at least 60 valid minutes, as in `calibration/physical_profiles.json`.", ""]
    for k, g in enumerate(GROUPS, 1):
        L += [f"## {k}. {GROUP_NAME[g]}", ""]
        L += _table_physical(metrics) if g == "physical" else _table_group(g, metrics)
        L += [""] + [" ".join(_group_sentences(g, metrics))] + [""]
        L += [f"![{GROUP_NAME[g]}](figures/{g}.png)", ""]
        if g == "physical":
            L += ["*Figure: each column is a role; the grey band goes from the 10th to the 90th real percentile, the grey line is the real median; "
                  "the dots are the synthetic players (orange: SYN-001), the blue line is the synthetic median. The traffic light is under each column.*", ""]
        else:
            L += ["*Figure: in the histograms the grey band goes from the 10th to the 90th real percentile, the grey outline is the real distribution, "
                  "the blue bars the synthetic one; vertical lines: real median (dashed) and synthetic median (blue). In the dot panels, "
                  "one dot per synthetic match or team (orange: SYN-001) and the grey bar with the medians of the 6 real matches. "
                  "x axis from the 1st to the 99th percentile.*", ""]
        if g == "team_shape":
            L += ["### Mean positions by role", "", "![Mean positions](figures/positions.png)", "",
                  "*Figure: mean position of each role with the ball in play, the team attacking to the right; hollow circles: real DFL "
                  "(`calibration/position_maps.json`), filled circles: synthetic.*", "",
                  "| Role | Gap in possession | Gap out of possession |", "|---|---|---|"]
            for role, row in sorted(result["mean_positions"].items()):
                a, b = row["in_possession"]["distance_m"], row["out_of_possession"]["distance_m"]
                L.append(f"| {role} | {fmt(a, 'm') if a is not None else 'n/a'} | {fmt(b, 'm') if b is not None else 'n/a'} |")
            L += ["", "The gap is the distance between the synthetic and the real mean position of the same role (for information, no traffic light).", ""]
    L += ["## What to improve in the generator", "",
          "Yellow and red metrics (the physical metrics group the roles) plus the issues already known from the first realism check, ranked by how "
          "visible they are in a 2-minute video (shots and outcomes, then ball, team shape and speed, then rhythm and counts), then by severity.", ""]
    L += _improvements(result, match_id) or ["No yellow or red metric."]
    L += [""]
    tabs = [(f"Synthetic matches ({match_id} and replicas); rows: the simulation's label", result["shots_diagnostic"]["label_vs_observed_outcome"]),
            ("6 real DFL matches; rows: the provider's label", result["shots_diagnostic"].get("dfl_label_vs_observed_outcome") or {})]
    if any(ct for _, ct in tabs):
        L += ["### Shot outcomes: label against the outcome seen by the pipeline", "",
              "Columns: outcome rebuilt from the trajectories (`R-SHOT-OUTCOME`, `R-WOODWORK`; none = no consistent outcome or shot not located). "
              "It shows which outcomes the pipeline recognises, on synthetic and on real data.", ""]
        for title, ct in tabs:
            if not ct:
                continue
            obs = sorted({o for v in ct.values() for o in v})
            L += [f"{title}:", "", "| Label | " + " | ".join(obs) + " |", "|---|" + "---|" * len(obs)]
            for lab, v in ct.items():
                L.append(f"| {lab} | " + " | ".join(str(v.get(o, 0)) for o in obs) + " |")
            L += [""]
    groups = {}
    for key, m in metrics.items():
        if m["group"] == "physical" and m["name"] == "distance_per90_m":
            groups[m["role_group"]] = (m["synthetic"]["n"], m["dfl"]["n"])
    ref = meta["reference"]
    L += ["## Technical details", "",
          "- Role groups (synthetic / real player-matches): " + "; ".join(f"{ROLE_LONG[g]} {groups[g][0]} / {groups[g][1]}" for g in PHYS_GROUPS)
          + ". Midfielders join defensive, central and attacking midfielders: in the DFL data there are only 2 central and 9 attacking "
          "midfielders, too few for a distribution. Wide players include wingers and wide midfielders.",
          f"- Accelerations and decelerations: runs of valid samples above +{meta['realism_config']['acc_effort_ms2']:g} / below "
          f"-{meta['realism_config']['acc_effort_ms2']:g} m/s² for at least {meta['realism_config']['acc_effort_min_s']:g} s (a metric, never a rule "
          "to drop samples). Peak over 5 minutes: moving window with the player always on the pitch and at least "
          f"{100 * meta['realism_config']['peak_min_valid_frac']:g} % measured.",
          f"- Team shape: one frame per second with the ball in play, at least {meta['realism_config']['shape_min_outfield']} valid outfield "
          "players; length and width = maximum minus minimum. Possessions: spells of one team's possession with the ball in play, "
          f"{meta['realism_config']['possession_spell_min_s']:g} s and longer. Pass success: next touch by a team-mate (from the trajectory), "
          "by third of the kick point. Shot outcomes: the provider's label (DFL) or the simulation's (synthetic).",
          f"- Reference: `{ref['file']}` (sha256 `{ref['sha256'][:16]}`), matches {', '.join(ref['matches_used'])}; preparation configuration "
          f"{', '.join(ref['prep_config_hash'])}; same configuration on both sides: {'yes' if meta['same_preparation_config'] else 'NO'}.",
          f"- Reference quantiles (privacy): for each metric the real file holds only {len(ref['quantile_levels_pct'])} quantiles, "
          f"{_levels(ref['quantile_levels_pct'])}, plus n and the range of the medians per match. There is no minimum (p0) and no maximum "
          f"(p100). Known and accepted limit (guidelines, section 19; aggregates of open CC-BY data): with linear interpolation a quantile "
          "can coincide with the value of a single "
          f"player-match when its position p × (n − 1) / 100 is a whole number ({_exact_quantiles(ref, metrics)}), and the median per match "
          "of an odd number of player-matches is the value of one of them. The real distribution used for D is linear between these "
          f"quantiles, 0 below p{ref['quantile_levels_pct'][0]:g} and 1 from p{ref['quantile_levels_pct'][-1]:g}: against the full real "
          f"distribution D can change by at most {max(ref['quantile_levels_pct'][0], 100 - ref['quantile_levels_pct'][-1]) / 100:g} in the tails "
          f"and by at most {max(np.diff(ref['quantile_levels_pct'])) / 100:g} (the gap between two stored quantiles) in between. "
          "The traffic-light thresholds do not change. "
          f"In the histograms the real outline is drawn only between p{ref['quantile_levels_pct'][0]:g} and p{ref['quantile_levels_pct'][-1]:g}.",
          f"- Linesman configuration `{meta['realism_config_hash']}`; pipeline {meta['pipeline_version']}, guidelines {meta['guidelines_version']}; "
          "libraries: " + ", ".join(f"{k} {v}" for k, v in meta["library_versions"].items()) + ".",
          "- To rebuild the report: generate the replicas (`cd generator && .venv/bin/python -m insidethegame_gen.match "
          f"{match_id} --replicas {n_rep} --only-replicas`), then `{meta['generated_by']}`. The matches are prepared in the git-ignored folder "
          "`reports/qc/frames/_realism/`.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def write_outputs(result: dict, reference: dict, per_match: dict, out_dir: Path, cfg: RealismConfig, log=print) -> None:
    out_dir = Path(out_dir)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    match_id = result["meta"]["match_id"]
    (out_dir / "realism.json").write_text(dumps(result, cfg.float_decimals))
    figure_physical(result, reference, per_match, match_id, fig_dir / "physical.png")
    for g in GROUPS:
        if g != "physical":
            figure_group(g, result, reference, per_match, match_id, fig_dir / f"{g}.png")
    figure_positions(result, match_id, fig_dir / "positions.png")
    (out_dir / "README.md").write_text(readme(result, match_id))
    log(f"wrote {out_dir}/realism.json, README.md and {len(GROUPS) + 1} figures")


__all__ = ["write_outputs", "readme", "metric_catalogue"]
