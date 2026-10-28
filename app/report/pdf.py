"""The final match report in PDF: what the fan's version of the app shows after the final whistle, on A4, in English.

The report is built only from the payloads the app shows the same ticket in the same version, so it never prints a
number the app does not show: `MatchStats` (Data & Statistics), the Recap's key moments (`Highlight`, chosen by VAR),
the `Recap` (its summary) and, for names and colours, the match header and the Pre-match overview. It follows the rules
of web/src/sections/DataStats.tsx: a metric the version receives null or empty is left out, with the reason when the
payload lists it in `unavailable`; the charts and tables of the Analyst version are only named in Fan ("Available in
the Analyst plan"); each chart becomes a small table, home/away bars or a shaded grid. Left out as in the app: the
acceleration load and the reference values of the effective playing time. The appendix "How these numbers were
computed" (the Inspector's methods) is only for tickets with the Inspector. For SYN-001 the Fan report has 2 pages and
the Analyst one 4, plus the appendix.

The text uses the PDF core font Helvetica, so it needs no font file: text outside Latin-1 (curly quotes, dashes, other
scripts) is replaced by its closest Latin-1 form. Every printed text goes through `_Report.t`, which also records it
with its part of the report (the tests read it). Library: fpdf2 (pure Python, no system libraries in the container).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

INK = (24, 16, 38)
MUTED = (110, 100, 130)
VIOLET = (43, 26, 71)    # --surface-2 of the app
GREEN = (0, 160, 98)     # --accent of the app, darker for print
LINE = (215, 208, 228)
TRACK = (241, 238, 246)  # the empty part of a bar
PITCH = (176, 168, 190)  # pitch lines over the zone map
BOLD = FontFace(emphasis="BOLD")
DASH = "-"  # a missing value in a table (the app's dash)

MODES = {"casual": "FAN", "analyst": "ANALYST"}
LEFT_OUT = {"acceleration_load"}  # not reliable enough: left out of the app too
ANALYST_METHODS = {"speed_zones", "passes.avg_length_m", "zone_map", "defensive_shape", "ppda"}  # Analyst numbers only
LOCKED_TABLES = ["PPDA", "All players"]  # locked in Fan next to the charts of `locked_charts`
# Charts with a section of their own, in the payload's order. The peaks of peak_intensity and ball_circulation go
# into the Running and On the ball tables.
CHART_SECTIONS = {"momentum", "shot_map", "xg_cumulative", "zone_map", "speed_zones", "defensive_shape"}
OUTCOMES = {"goal": "Goal", "saved": "Saved", "wide": "Wide", "blocked": "Blocked", "woodwork": "Woodwork"}
ZONES = ("0-7", "7-15", "15-20", "20-25", ">25")
ZONE_KEYS = ("km_0_7", "km_7_15", "km_15_20", "km_20_25", "km_over_25")
COUNTS = (("Corners", "corners"), ("Fouls", "fouls"), ("Yellow cards", "yellow_cards"), ("Red cards", "red_cards"),
          ("Penalties", "penalties"), ("Woodwork", "woodwork"), ("Offsides", "offsides"))
SPLITS = (("Distance, ball in play", "ball_in_play_km"), ("Distance, ball out of play", "ball_stopped_km"),
          ("Distance in possession", "in_possession_km"), ("Distance out of possession", "out_of_possession_km"))
LABELS = {  # metric ids of `unavailable` and of the Inspector's methods
    "as_of_s": "No look-ahead", "shots": "Shots", "counts": "Set pieces, fouls and cards",
    "leaders.most_touches": "Most touches", "passes": "Passes", "distance_covered": "Distance covered",
    "top_speed": "Top speed", "speed_zones": "Distance by speed zone", "counts.sprints": "Sprints",
    "peak_intensity": "Running intensity", "ball_circulation": "Ball circulation",
    "passes.avg_length_m": "Pass length and long balls", "passes.long_balls": "Long balls", "possession": "Possession",
    "shot_speed": "Fastest shot", "counts.ball_recoveries": "Tackles and ball recoveries",
    "counts.tackles": "Tackles and ball recoveries", "counter_attacks": "Counter-attacks", "zone_map": "Zones of play",
    "defensive_shape": "Defensive line and team length", "ppda": "PPDA", "shot_map": "Shot map",
    "xg": "Expected goals (xG)", "goals_vs_xg": "Goals vs xG", "momentum": "Who is pushing",
    "effective_playing_time": "Effective playing time",
}

_LATIN1 = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         "…": "...", "≈": "~", "′": "'", " ": " ", " ": " ", " ": " "})


def _t(text: object) -> str:
    """Text for the core font: Latin-1 only (see the module docstring)."""
    return str(text).translate(_LATIN1).encode("latin-1", "replace").decode("latin-1")


def report_filename(recap: dict) -> str:
    return f"statseat-report-{recap['match_id']}-{recap['mode']}.pdf"


def _rgb(hexa: str) -> tuple[int, int, int]:
    h = hexa.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(colour: tuple[int, int, int], share: float) -> tuple[int, int, int]:
    """`colour` over white at `share` opacity (the app's zone map)."""
    return tuple(round(255 + (c - 255) * share) for c in colour)  # type: ignore[return-value]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _bucket(minute: int) -> str:
    """A five-minute bucket of the series, which are labelled with their last minute."""
    return f"{max(minute - 5, 0)}'-{minute}'"


class _Report(FPDF):
    """An A4 report that records every text it prints, with the part of the report it belongs to."""

    footer_text = ""

    def __init__(self) -> None:
        super().__init__(format="A4")
        self.part = "header"
        self.printed: list[tuple[str, str]] = []

    def t(self, text: object) -> str:
        """Text for the core font (`_t`), recorded in `printed`."""
        s = _t(text)
        self.printed.append((self.part, s))
        return s

    def footer(self) -> None:
        part, self.part = self.part, "footer"
        self.set_y(-14)
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, self.t(self.footer_text), align="L")
        self.cell(0, 5, self.t(f"{self.page_no()}/{{nb}}"), align="R")
        self.part = part


class _Builder:
    """Lays the report out part by part: the Recap's summary and key moments, then Data & Statistics (the match table,
    the charts, the other tables, the players), then the Inspector's methods."""

    L, W = 16.0, 178.0          # left margin and width of the text area (mm)
    ROW = 4.8                   # a team-against-team row
    LABEL_W, VALUE_W = 56.0, 20.0
    HALF = 38.0                 # one team's bar
    MID = L + LABEL_W + VALUE_W + 2 + HALF + 1  # between the two bars
    AWAY_X = L + W - VALUE_W

    def __init__(self, recap: dict, stats: dict, highlights: list[dict], match: dict, overview: dict,
                 generated_at: datetime) -> None:
        self.recap, self.s, self.moments, self.match = recap, stats, highlights, match
        self.analyst = stats["mode"] == "analyst"
        self.h, self.a = match["home_team_id"], match["away_team_id"]
        self.teams = {t["team_id"]: t for t in overview["teams"]}
        self.names = {p["player_id"]: p["name"] for t in overview["teams"] for p in t["lineup"]}
        self.pdf = _Report()
        self.pdf.footer_text = ("Every number is computed by deterministic code from the match data, as in the app. "
                                f"Generated {generated_at:%d %b %Y, %H:%M}.")

    def build(self) -> _Report:
        pdf = self.pdf
        pdf.set_auto_page_break(True, margin=18)
        pdf.set_margins(self.L, 16, self.L)
        pdf.add_page()
        self.header()
        self.summary()
        self.key_moments()
        self.match_numbers()
        self.charts()
        self.running()
        self.ball()
        self.defence()
        self.favourites()
        self.all_players()
        self.locked()
        self.appendix()
        return pdf

    # ------------------------------------------------------------ names and colours

    def short(self, team_id: str) -> str:
        t = self.teams.get(team_id) or {}
        return t.get("short_name") or t.get("name") or str(team_id)

    def colour(self, team_id: str | None) -> tuple[int, int, int]:
        hexa = ((self.teams.get(team_id) or {}).get("colours") or {}).get("primary") if team_id else None
        return _rgb(hexa) if hexa else INK

    def team_style(self, team_id: str) -> FontFace:
        return FontFace(emphasis="BOLD", color=self.colour(team_id))

    def name(self, player_id: str) -> str:
        return self.names.get(player_id, player_id)

    @staticmethod
    def of(items: list[dict] | None, team_id: str) -> dict | None:
        return next((x for x in items or [] if x.get("team_id") == team_id), None)

    def label(self, metric: str) -> str:
        if metric.startswith("ppda."):
            return f"PPDA of {self.short(metric.split('.', 1)[1])}"
        return LABELS.get(metric, metric.replace("_", " ").capitalize())

    # ------------------------------------------------------------ building blocks

    def need(self, height: float) -> None:
        """A new page when `height` mm do not fit on this one."""
        if self.pdf.will_page_break(height):
            self.pdf.add_page()

    def heading(self, text: str, part: str, keep: float = 16.0) -> None:
        """A section title with the green rule; `keep` mm of the section stay on the same page."""
        pdf = self.pdf
        pdf.part = part
        self.need(12 + keep)
        pdf.ln(3)
        pdf.set_x(self.L)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*VIOLET)
        pdf.cell(0, 6.5, pdf.t(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(*GREEN)
        pdf.set_line_width(0.6)
        pdf.line(self.L, pdf.get_y(), self.L + 24, pdf.get_y())
        pdf.ln(2)

    def note(self, text: str) -> None:
        pdf = self.pdf
        pdf.set_x(self.L)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(self.W, 4, pdf.t(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def caption(self, text: str) -> None:
        """A small line under the bars, centred (as the app's hints)."""
        pdf = self.pdf
        self.need(self.ROW)
        pdf.set_x(self.L)
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(self.W, 4, pdf.t(text), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def unavailable(self, *metrics: str) -> None:
        """'<metrics> not available: <reason>.' for the metrics of this part that the payload lists in `unavailable`,
        one note per reason (the app's 'Not available')."""
        by_reason: dict[str, list[str]] = {}
        for u in self.s.get("unavailable") or []:
            m = u["metric"]
            if m in metrics or (m.startswith("ppda.") and "ppda" in metrics):
                labels = by_reason.setdefault(u["reason"], [])
                if self.label(m) not in labels:
                    labels.append(self.label(m))
        for reason, labels in by_reason.items():
            self.note(f"{_join(labels)} not available: {reason}.")

    def sides(self, label: str, home: str | None, away: str | None, bold: bool = False) -> None:
        """A label and one text per team in its colour, either side of the middle of the bars (leaders, team names).
        Nothing when both texts are missing."""
        if home is None and away is None:
            return
        pdf = self.pdf
        self.need(self.ROW)
        y = pdf.get_y()
        pdf.set_font("Helvetica", "B" if bold else "", 8.5)
        if label:
            pdf.set_text_color(*INK)
            pdf.set_xy(self.L, y)
            pdf.cell(self.LABEL_W, self.ROW, pdf.t(label))
        for team, text, x, w, align in ((self.h, home, self.L + self.LABEL_W, self.MID - 2 - self.L - self.LABEL_W, "R"),
                                        (self.a, away, self.MID + 2, self.L + self.W - self.MID - 2, "L")):
            if text:
                pdf.set_text_color(*self.colour(team))
                pdf.set_xy(x, y)
                pdf.cell(w, self.ROW, pdf.t(text), align=align)
        pdf.set_xy(self.L, y + self.ROW)

    def team_row(self) -> None:
        self.sides("", self.short(self.h), self.short(self.a), bold=True)

    def pair(self, label: str, home: float | None, away: float | None, fmt: Callable[[float], str] = str,
             home_text: str | None = None, away_text: str | None = None) -> None:
        """A team-against-team row as the app's PairRow: the label, the home value, two bars from the middle
        (proportional to the larger value), the away value. Nothing when a value is missing, as in the app."""
        if home is None or away is None:
            return
        pdf = self.pdf
        self.need(self.ROW)
        y = pdf.get_y()
        pdf.set_font("Helvetica", size=8.5)
        pdf.set_text_color(*INK)
        pdf.set_xy(self.L, y)
        pdf.cell(self.LABEL_W, self.ROW, pdf.t(label))
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_xy(self.L + self.LABEL_W, y)
        pdf.cell(self.VALUE_W, self.ROW, pdf.t(home_text if home_text is not None else fmt(home)), align="R")
        pdf.set_xy(self.AWAY_X, y)
        pdf.cell(self.VALUE_W, self.ROW, pdf.t(away_text if away_text is not None else fmt(away)), align="L")
        top = max(home, away) or 1
        bar_y, bar_h = y + (self.ROW - 2.2) / 2, 2.2
        pdf.set_fill_color(*TRACK)
        pdf.rect(self.MID - 1 - self.HALF, bar_y, self.HALF, bar_h, style="F")
        pdf.rect(self.MID + 1, bar_y, self.HALF, bar_h, style="F")
        for team, value, sign in ((self.h, home, -1), (self.a, away, 1)):
            w = self.HALF * value / top
            if w > 0:
                pdf.set_fill_color(*self.colour(team))
                pdf.rect(self.MID - 1 - w if sign < 0 else self.MID + 1, bar_y, w, bar_h, style="F")
        pdf.set_xy(self.L, y + self.ROW)

    def table(self, header: list, rows: list[list], widths: list[float], aligns: tuple[str, ...] | None = None,
              size: float = 8.0, padding: tuple[float, float] | None = None) -> None:
        """A table with a heading row and thin lines between rows; a cell is a text or (text, FontFace). `padding`
        (vertical, horizontal mm) replaces the default cell margin, for tables with many columns."""
        pdf = self.pdf
        pdf.set_x(self.L)
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.2)
        pdf.set_fill_color(255, 255, 255)  # fpdf2 paints the cells with the current fill colour
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", size=size)
        aligns = aligns or tuple(["LEFT"] + ["RIGHT"] * (len(header) - 1))
        headings = FontFace(emphasis="BOLD", color=MUTED, size_pt=size - 0.5)
        with pdf.table(col_widths=widths, text_align=aligns, line_height=size * 0.5, first_row_as_headings=True,
                       borders_layout="HORIZONTAL_LINES", headings_style=headings, cell_fill_mode="NONE",
                       padding=padding) as tb:
            for r in [header, *rows]:
                row = tb.row()
                for c in r:
                    text, style = c if isinstance(c, tuple) else (c, None)
                    row.cell(pdf.t(text), style=style)

    # ------------------------------------------------------------ the Recap

    def header(self) -> None:
        """The violet band: app, report and version, the final score, the match."""
        pdf, m, score = self.pdf, self.match, self.recap["final_score"]
        pdf.part = "header"
        pdf.set_fill_color(*VIOLET)
        pdf.rect(0, 0, pdf.w, 40, style="F")
        pdf.set_xy(self.L, 10)
        pdf.set_text_color(*GREEN)
        pdf.set_font("Helvetica", "B", 9)
        mode = MODES.get(self.s["mode"], self.s["mode"].upper())
        pdf.cell(0, 5, pdf.t(f"statseat  |  MATCH REPORT  |  {mode}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 20)
        pdf.cell(0, 11, pdf.t(f"{m['home_name']}  {score['home']} - {score['away']}  {m['away_name']}"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 6, pdf.t(f"{m['title']}  |  {m['stadium_name']}  |  "
                             f"{datetime.fromisoformat(str(m['kickoff'])):%d %B %Y, %H:%M}"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_y(47)

    def summary(self) -> None:
        """The Recap's summary: a text the code writes from the numbers (no model)."""
        pdf = self.pdf
        pdf.part = "summary"
        pdf.set_x(self.L)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", size=10.5)
        pdf.multi_cell(self.W, 5.4, pdf.t(self.recap["narrative"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "I", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5.5, pdf.t("Summary computed from the match data."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def key_moments(self) -> None:
        """VAR's moments of the Recap, in its order: the label (bold for goals, in the team colour) and why it
        mattered."""
        self.heading("Key moments", "key_moments")
        if not self.moments:
            self.note("No key moments for this match.")
            return
        rows = [[(m["label"], FontFace(emphasis="BOLD" if m["type"] == "goal" else None, color=self.colour(m["team_id"]))),
                 m["why_it_mattered"]] for m in self.moments]
        self.table(["Moment", "Why it mattered"], rows, [74, 104], aligns=("LEFT", "LEFT"))

    # ------------------------------------------------------------ Data & Statistics: the match table

    def match_numbers(self) -> None:
        """The match table of Data & Statistics, without the reference values of the effective playing time."""
        s, h, a = self.s, self.h, self.a
        self.heading("The match in numbers", "match")
        self.team_row()
        sh, sa = self.of(s["shots"], h) or {}, self.of(s["shots"], a) or {}
        self.pair("Goals", sh.get("goals"), sa.get("goals"))
        self.pair("Shots", sh.get("shots"), sa.get("shots"))
        self.pair("Shots on target", sh.get("on_target"), sa.get("on_target"))
        poss = (s.get("possession") or {}).get("teams")
        self.pair("Possession", (self.of(poss, h) or {}).get("value"), (self.of(poss, a) or {}).get("value"),
                  fmt=lambda v: f"{v:.0f}%")
        ch, ca = self.of(s["counts"], h) or {}, self.of(s["counts"], a) or {}
        for label, key in COUNTS:
            self.pair(label, ch.get(key), ca.get(key))
        gh, ga = self.of(s.get("goals_vs_xg"), h), self.of(s.get("goals_vs_xg"), a)
        if gh and ga:
            self.sides("Goals vs xG", *(f"Goals {g['goals']} · xG {g['xg']:.2f}" for g in (gh, ga)))
        e = s.get("effective_playing_time")
        if e:
            text = f"Ball in play {e['total_min']:.0f} of {e['played_min']:.0f} min"
            if e.get("first_half_min") is not None and e.get("second_half_min") is not None:
                text += f" (1st half {e['first_half_min']:.0f} min · 2nd half {e['second_half_min']:.0f} min)"
            elif e.get("first_half_min") is not None:
                text += f" (1st half {e['first_half_min']:.0f} min)"
            self.line("Effective playing time", text)
        self.unavailable("possession", "goals_vs_xg", "effective_playing_time")

    def line(self, label: str, text: str) -> None:
        """A label and one text for the whole match."""
        pdf = self.pdf
        self.need(self.ROW)
        y = pdf.get_y()
        pdf.set_font("Helvetica", size=8.5)
        pdf.set_text_color(*INK)
        pdf.set_xy(self.L, y)
        pdf.cell(self.LABEL_W, self.ROW, pdf.t(label))
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(self.W - self.LABEL_W, self.ROW, pdf.t(text))
        pdf.set_xy(self.L, y + self.ROW)

    # ------------------------------------------------------------ Data & Statistics: the charts

    def charts(self) -> None:
        """The charts of this version (`charts`, in their order), each as a small table, bars or a shaded grid."""
        mode = self.s["mode"]
        for c in self.s.get("charts") or []:
            if c["chart_id"] in CHART_SECTIONS and mode in c.get("modes", [mode]):
                getattr(self, f"chart_{c['chart_id']}")(c["title"])
        self.pdf.part = "charts"
        self.unavailable("momentum", "xg", "zone_map", "speed_zones", "defensive_shape")

    def chart_momentum(self, title: str) -> None:
        """Who is pushing: one bar per minute, up for the home team and down for the away team (the mirror chart),
        with the goals as vertical lines."""
        series = (self.s.get("momentum") or {}).get("series") or []
        self.heading(title, "chart:momentum", keep=30)
        pdf = self.pdf
        half, names_w = 8.0, 26.0
        x0, w = self.L + names_w, self.W - names_w
        last = max([self.s.get("up_to_minute") or 0] + [p["minute"] for p in series]) or 1
        step = w / last
        y = pdf.get_y() + 1
        mid = y + half
        pdf.set_font("Helvetica", "B", 8)
        for team, ty in ((self.h, mid - 5), (self.a, mid + 1)):
            pdf.set_text_color(*self.colour(team))
            pdf.set_xy(self.L, ty)
            pdf.cell(names_w - 2, 4, pdf.t(self.short(team)))
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.2)
        pdf.line(x0, mid, x0 + w, mid)
        bar = step * 0.8
        for p in series:
            v = max(-1.0, min(1.0, p["value"]))
            if v:
                pdf.set_fill_color(*self.colour(self.h if v > 0 else self.a))
                pdf.rect(x0 + (p["minute"] - 1) * step + (step - bar) / 2, mid - v * half if v > 0 else mid, bar,
                         abs(v) * half, style="F")
        pdf.set_line_width(0.35)
        for g in self.s.get("goal_marks") or []:
            gx = x0 + (g["minute"] - 0.5) * step
            pdf.set_draw_color(*self.colour(g["team_id"]))
            pdf.line(gx, y - 1, gx, y + 2 * half + 1)
        pdf.set_font("Helvetica", size=6.5)
        pdf.set_text_color(*MUTED)
        if series:  # the first and the last minute, as the app's axis
            for m, align in ((series[0]["minute"], "L"), (last, "R")):
                pdf.set_xy(x0 if align == "L" else x0 + w - 20, y + 2 * half + 1)
                pdf.cell(20, 3, pdf.t(f"{m}'"), align=align)
        pdf.set_xy(self.L, y + 2 * half + 4.5)
        self.note(f"One bar per minute: up when {self.short(self.h)} push, down when {self.short(self.a)} push. "
                  "Vertical lines: the goals.")

    def chart_shot_map(self, title: str) -> None:
        """The shots of each team side by side: minute, player, outcome and, in Analyst, the xG (the app's shot
        map)."""
        shots = (self.s.get("shot_map") or {}).get("shots") or []
        sides = [[x for x in shots if x["team_id"] == t] for t in (self.h, self.a)]
        n = max(len(sides[0]), len(sides[1]))
        self.heading("Shots", "chart:shot_map", keep=min(n, 4) * 4 + 5)
        if not n:
            self.note("No shots in this match.")
            return
        width = 4 if self.analyst else 3

        def cells(x: dict | None) -> list:
            if x is None:
                return [""] * width
            out = OUTCOMES.get(x["outcome"], x["outcome"].capitalize())
            out += " (pen.)" if x.get("is_penalty") else " (header)" if x.get("is_header") else ""
            style = BOLD if x["outcome"] == "goal" else None
            row = [(f"{x['minute']}'", style), (self.name(x["player_id"]), style), (out, style)]
            if self.analyst:
                row.append((DASH if x.get("xg") is None else f"{x['xg']:.2f}", style))
            return row

        header: list = []
        for t in (self.h, self.a):
            header += ["Min", (self.short(t), self.team_style(t)), "Outcome"] + (["xG"] if self.analyst else []) + [""]
        rows = [cells(sides[0][i] if i < len(sides[0]) else None) + [""] +
                cells(sides[1][i] if i < len(sides[1]) else None) for i in range(n)]
        side_w = [9, 42, 25, 11] if self.analyst else [9, 52, 26]
        side_al = ("LEFT", "LEFT", "LEFT", "RIGHT")[:width]
        self.table(header[:-1], rows, [*side_w, 4, *side_w], aligns=(*side_al, "LEFT", *side_al))

    def chart_xg_cumulative(self, title: str) -> None:
        """Expected goals over time: each team's xG at half-time and at the end (points of the cumulative series)."""
        x = self.s.get("xg") or {}
        self.heading(title, "chart:xg_cumulative")
        self.team_row()

        def first_half(team: str) -> float | None:
            pts = [p["value"] for p in (self.of(x.get("cumulative"), team) or {}).get("points") or [] if p["minute"] <= 45]
            return pts[-1] if pts else None

        th, ta = self.of(x.get("teams"), self.h), self.of(x.get("teams"), self.a)
        self.pair("xG, first half", first_half(self.h), first_half(self.a), fmt=lambda v: f"{v:.2f}")
        self.pair("xG, whole match", th and th["value"], ta and ta["value"], fmt=lambda v: f"{v:.2f}")

    def chart_zone_map(self, title: str) -> None:
        """Zones of play: each team's share of time per cell, shaded in its colour (the app's zone map), attacking to
        the right."""
        zones = [z for z in self.s.get("zone_map") or [] if not z.get("player_id")]
        gap = 6.0
        pw = (self.W - gap) / 2
        ph = pw * 68 / 105
        self.heading(title, "chart:zone_map", keep=ph + 6)
        pdf = self.pdf
        y = pdf.get_y() + 1
        for i, team in enumerate((self.h, self.a)):
            z = self.of(zones, team)
            if not z:
                continue
            x0 = self.L + i * (pw + gap)
            top = max([v for row in z["cells_pct"] for v in row] + [1.0])
            cw, ch = pw / z["cols"], ph / z["rows"]
            for r, row in enumerate(z["cells_pct"]):
                for c, v in enumerate(row):
                    if v > 0:
                        pdf.set_fill_color(*_mix(self.colour(team), 0.85 * v / top))
                        pdf.rect(x0 + c * cw, y + r * ch, cw + 0.05, ch + 0.05, style="F")
            self.pitch(x0, y, pw, ph)
            pdf.set_xy(x0, y + ph + 0.8)
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(*self.colour(team))
            pdf.cell(pw, 4, pdf.t(f"{self.short(team)}, attacking to the right"))
        pdf.set_xy(self.L, y + ph + 5.5)

    def pitch(self, x: float, y: float, w: float, h: float) -> None:
        """The lines of a 105 x 68 m pitch (outline, halfway line, penalty areas) over a zone map."""
        pdf = self.pdf
        sx, sy = w / 105, h / 68
        pdf.set_draw_color(*PITCH)
        pdf.set_line_width(0.25)
        pdf.rect(x, y, w, h)
        pdf.line(x + w / 2, y, x + w / 2, y + h)
        box_w, box_h = 16.5 * sx, 40.32 * sy
        pdf.rect(x, y + (h - box_h) / 2, box_w, box_h)
        pdf.rect(x + w - box_w, y + (h - box_h) / 2, box_w, box_h)

    def chart_speed_zones(self, title: str) -> None:
        """Distance by speed zone: each team's km per FIFA speed zone (the app's grouped bars)."""
        rows = []
        for team in (self.h, self.a):
            z = next((x for x in self.s.get("speed_zones") or [] if x["team_id"] == team and not x.get("player_id")), None)
            if z:
                rows.append([(self.short(team), self.team_style(team)), *(f"{z[k]:.1f}" for k in ZONE_KEYS)])
        self.heading(title, "chart:speed_zones")
        self.table(["km per speed zone (km/h)", *ZONES], rows, [58, 24, 24, 24, 24, 24])

    def chart_defensive_shape(self, title: str) -> None:
        """Defensive line and team length: each team's lowest and highest five minutes (points of the series)."""
        rows = []
        for d in self.s.get("defensive_shape") or []:
            row = [(self.short(d["team_id"]), self.team_style(d["team_id"]))]
            for key in ("defensive_line_m", "team_length_m"):
                pts = d.get(key) or []
                lo = min(pts, key=lambda p: (p["value"], p["minute"]), default=None)
                hi = max(pts, key=lambda p: (p["value"], -p["minute"]), default=None)
                row += [DASH if p is None else f"{p['value']:.1f} m, {_bucket(p['minute'])}" for p in (lo, hi)]
            rows.append(row)
        rows.sort(key=lambda r: r[0][0] != self.short(self.h))
        self.heading(title, "chart:defensive_shape")
        self.table(["", "Line, lowest", "Line, highest", "Length, shortest", "Length, longest"], rows,
                   [34, 36, 36, 36, 36])
        self.note("Averages per five minutes of play: the defensive line from the team's own goal line.")

    # ------------------------------------------------------------ Data & Statistics: the other tables

    def leaders(self, label: str, key: str, fmt: Callable[[float], str]) -> None:
        def text(team: str) -> str | None:
            x = (self.of(self.s.get("leaders"), team) or {}).get(key)
            return f"{self.name(x['player_id'])} · {fmt(x['value'])}" if x else None

        self.sides(label, text(self.h), text(self.a))

    def running(self) -> None:
        """The running table: distance, top speed, sprints and the most intense five minutes."""
        s, h, a = self.s, self.h, self.a
        self.heading("Running", "running")
        self.team_row()
        d = s.get("distance_covered")
        dh, da = self.of((d or {}).get("teams"), h), self.of((d or {}).get("teams"), a)
        if dh and da:
            def km(x: dict) -> str:
                return f"{'~' if x['distance']['approx'] else ''}{x['distance']['km']:.1f}"

            self.pair("Distance covered (km)", dh["distance"]["km"], da["distance"]["km"], home_text=km(dh),
                      away_text=km(da))
            self.leaders("Top runner", "top_runner", lambda v: f"{v:.2f} km")
            if dh.get("splits") and da.get("splits"):
                for label, key in SPLITS:
                    self.pair(label, dh["splits"][key], da["splits"][key], fmt=lambda v: f"{v:.1f} km")
        speeds = s.get("top_speed") or []
        by_team = [[x for x in speeds if x["team_id"] == t] for t in (h, a)]
        if by_team[0] and by_team[1]:
            self.pair("Top speed (km/h)", by_team[0][0]["kmh"], by_team[1][0]["kmh"], fmt=lambda v: f"{v:.1f}")
        if self.analyst:
            for i in range(max(len(by_team[0]), len(by_team[1]))):
                self.sides("Fastest" if i == 0 else "",
                           *(f"{self.name(x[i]['player_id'])} · {x[i]['kmh']:.1f} km/h · {x[i]['minute']}'"
                             if i < len(x) else None for x in by_team))
        else:
            self.sides("Fastest", *(f"{self.name(x[0]['player_id'])} · {x[0]['minute']}'" if x else None for x in by_team))
        ch, ca = self.of(s["counts"], h) or {}, self.of(s["counts"], a) or {}
        self.pair("Sprints", ch.get("sprints"), ca.get("sprints"))
        self.leaders("Most sprints", "top_sprinter", lambda v: f"{v:.0f}")
        peaks = (s.get("peak_intensity") or {}).get("teams")
        self.sides("Most intense 5 minutes", *(
            f"{p['start_minute']}'-{p['end_minute']}' · {p['metres_per_minute']:.1f} m/min" if p else None
            for p in (self.of(peaks, h), self.of(peaks, a))))
        self.unavailable("distance_covered", "top_speed", "counts.sprints", "peak_intensity")

    def ball(self) -> None:
        """The table on the ball: passes, ball circulation, the fastest shot, counter-attacks and touches."""
        s, h, a = self.s, self.h, self.a
        self.heading("On the ball", "ball")
        self.team_row()
        ph, pa = self.of(s.get("passes"), h), self.of(s.get("passes"), a)
        if ph and pa:
            self.pair("Passes", ph["passes"], pa["passes"])
            self.pair("Pass accuracy", ph["accuracy_pct"], pa["accuracy_pct"], fmt=lambda v: f"{v:.1f}%")
            self.leaders("Most passes", "top_passer", lambda v: f"{v:.0f}")
            self.pair("Average pass length", ph.get("avg_length_m"), pa.get("avg_length_m"), fmt=lambda v: f"{v:.1f} m")
            self.pair("Long balls", ph.get("long_balls"), pa.get("long_balls"))
        circ = s.get("ball_circulation") or {}
        ch, ca = self.of(circ.get("teams"), h), self.of(circ.get("teams"), a)
        if ch and ca:
            self.pair("Ball circulation (km/h)", ch["value"], ca["value"], fmt=lambda v: f"{v:.1f}")
            if ch["value"] != ca["value"]:
                self.caption(f"{self.short(h if ch['value'] > ca['value'] else a)} move the ball faster")

        def fastest(team: str) -> str | None:  # the Analyst chart of the ball circulation: its fastest five minutes
            pts = (self.of(circ.get("series"), team) or {}).get("points") or []
            p = max(pts, key=lambda q: (q["value"], -q["minute"]), default=None)
            return f"{p['value']:.1f} km/h · {_bucket(p['minute'])}" if p else None

        self.sides("Fastest 5 minutes of circulation", fastest(h), fastest(a))
        self.sides("Fastest shot", *(f"{self.name(x['player_id'])} · {x['kmh']:.0f} km/h · {x['minute']}'" if x else None
                                     for x in (self.of(s.get("shot_speed"), h), self.of(s.get("shot_speed"), a))))
        counter = s.get("counter_attacks")
        if counter:
            cth, cta = self.of(counter["teams"], h), self.of(counter["teams"], a)
            self.pair("Counter-attacks", cth and cth["count"], cta and cta["count"])
            self.sides("Counter-attack minutes", *(", ".join(f"{x['minute']}'" for x in counter.get("items") or []
                                                             if x["team_id"] == t) or None for t in (h, a)))
        touches = [(self.of(s.get("leaders"), t) or {}).get("most_touches") or [] for t in (h, a)]
        for i in range(max(len(touches[0]), len(touches[1]))):
            self.sides("Most touches" if i == 0 else "", *(f"{self.name(x[i]['player_id'])} · {x[i]['value']:.0f}"
                                                           if i < len(x) else None for x in touches))
        self.unavailable("passes", "passes.long_balls", "passes.avg_length_m", "ball_circulation", "shot_speed",
                         "counter_attacks")

    def defence(self) -> None:
        """The defence table: tackles, ball recoveries and, in Analyst, PPDA."""
        s, h, a = self.s, self.h, self.a
        self.heading("Defence", "defence")
        self.team_row()
        ch, ca = self.of(s["counts"], h) or {}, self.of(s["counts"], a) or {}
        self.pair("Tackles", ch.get("tackles"), ca.get("tackles"))
        self.pair("Ball recoveries", ch.get("ball_recoveries"), ca.get("ball_recoveries"))
        if self.analyst:
            ph, pa = self.of(s.get("ppda"), h), self.of(s.get("ppda"), a)
            if ph and pa:
                self.pair("PPDA", ph["value"], pa["value"], fmt=lambda v: f"{v:.1f}")
                self.caption("Opponent passes per defensive action: lower means more pressing")
            self.unavailable("counts.tackles", "ppda")
        else:
            self.unavailable("counts.tackles")

    # ------------------------------------------------------------ Data & Statistics: the players

    def favourites(self) -> None:
        """The Player Focus cards of the fan's players: the Fan numbers, plus xG and speed zones in Analyst."""
        focus = self.s.get("player_focus") or []
        self.heading("Your players", "players", keep=14)
        if not focus:
            self.note("No favourite players were chosen for this match.")
            return

        def n(v: float | None, digits: int = 0) -> str:
            return DASH if v is None else f"{v:.{digits}f}"

        header = ["Player", "Min", "km", "Top km/h", "Sprints", "Passes", "Shots · goals", "Touches",
                  "Tackles · recov.", "Fouls · won", "Yellow · red"]
        widths = [34, 9, 15, 19, 13, 17, 15, 13, 16, 14, 13]
        if self.analyst:
            header.append("xG")
            widths = [31, 9, 15, 18, 12, 16, 14, 13, 16, 13, 11, 10]
        rows = []
        for f in focus:
            d = f.get("distance")
            top = n(f.get("top_speed_kmh"), 1)
            if f.get("top_speed_kmh") is not None and f.get("top_speed_minute") is not None:
                top += f" · {f['top_speed_minute']}'"
            passes = n(f.get("passes"))
            if f.get("passes") is not None and f.get("pass_accuracy_pct") is not None:
                passes += f" · {f['pass_accuracy_pct']:.0f}%"
            row = [(self.name(f["player_id"]), self.team_style(f["team_id"])), str(f["minutes_played"]),
                   f"{'~' if d['approx'] else ''}{d['km']:.2f}" if d else DASH, top, n(f.get("sprints")), passes,
                   f"{f.get('shots') or 0} · {f.get('goals') or 0}", n(f.get("touches")),
                   f"{n(f.get('tackles'))} · {n(f.get('ball_recoveries'))}",
                   f"{f.get('fouls_committed') or 0} · {f.get('fouls_won') or 0}",
                   f"{f.get('yellow_cards') or 0} · {f.get('red_cards') or 0}"]
            if self.analyst:
                row.append(n(f.get("xg"), 2))
            rows.append(row)
        self.table(header, rows, widths)
        zone_rows = [[(self.name(f["player_id"]), self.team_style(f["team_id"])),
                      *(f"{f['speed_zones'][k]:.2f}" for k in ZONE_KEYS)]
                     for f in focus if self.analyst and f.get("speed_zones")]
        if zone_rows:
            self.pdf.ln(2)
            self.table(["km per speed zone (km/h)", *ZONES], zone_rows, [58, 24, 24, 24, 24, 24])
        reason = next((u["reason"] for u in self.s.get("unavailable") or [] if u["metric"] == "distance_covered"), None)
        if reason and any(f.get("distance") is None for f in focus):
            self.note(f"Distance, top speed and sprints not available: {reason}.")

    def all_players(self) -> None:
        """The Analyst table of all players, home team first and by minutes played (as the app)."""
        players = self.s.get("players")
        if players is None:
            return
        self.heading("All players", "players_table", keep=20)
        if not players:
            self.note("No player numbers yet.")
            return

        def n(v: float | None, digits: int = 0) -> str:
            return DASH if v is None else f"{v:.{digits}f}"

        rows = []
        for p in sorted(players, key=lambda p: (p["team_id"] != self.h, -p["minutes_played"])):
            d = p.get("distance")
            rows.append([(self.name(p["player_id"]), FontFace(color=self.colour(p["team_id"]))), str(p["minutes_played"]),
                         f"{'~' if d['approx'] else ''}{d['km']:.2f}" if d else DASH, n(p.get("top_speed_kmh"), 1),
                         n(p.get("sprints")), n(p.get("peak_m_per_min"), 1), n(p.get("passes")),
                         n(p.get("pass_accuracy_pct")), n(p.get("long_balls")), n(p.get("touches")),
                         str(p.get("shots") or 0), str(p.get("goals") or 0), n(p.get("xg"), 2), n(p.get("tackles")),
                         n(p.get("ball_recoveries")), str(p.get("fouls_committed") or 0), str(p.get("fouls_won") or 0)])
        header = ["Player", "Min", "km", "Top km/h", "Sprints", "Peak m/min", "Passes", "Acc %", "Long", "Touches",
                  "Shots", "Goals", "xG", "Tackles", "Recov.", "Fouls", "Fouled"]
        widths = [34.5, 7, 10.5, 9, 10, 9.5, 10, 9, 8, 11, 8, 8, 7.5, 10, 9.5, 7.5, 9]
        self.table(header, rows, widths, size=7, padding=(0, 0.5))

    def locked(self) -> None:
        """Fan: the charts and tables of the Analyst version, named as the app's locked cards."""
        if self.analyst:
            return
        titles = [c["title"] for c in self.s.get("locked_charts") or [] if c["chart_id"] not in LEFT_OUT]
        self.pdf.part = "locked"
        self.need(10)
        self.pdf.ln(3)
        self.note("Available in the Analyst plan: " + ", ".join(titles + LOCKED_TABLES) + ".")

    # ------------------------------------------------------------ the Inspector

    def appendix(self) -> None:
        """How these numbers were computed: the Inspector's method and data excluded for each metric of this version,
        on a page of its own (tickets with the Inspector only)."""
        ins = self.s.get("inspector")
        if not ins:
            return
        pdf = self.pdf
        pdf.add_page()
        self.heading("How these numbers were computed", "appendix", keep=0)
        self.note(f"Source: {ins['source']}. Every number is computed by deterministic code; '~' marks a distance with "
                  "part of the time not measured.")
        pdf.ln(1)
        for m in ins["methods"]:
            if m["metric"] in LEFT_OUT or (not self.analyst and m["metric"] in ANALYST_METHODS):
                continue
            self.need(11)
            pdf.set_x(self.L)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", "B", 8)
            label = pdf.t(self.label(m["metric"]))
            pdf.cell(pdf.get_string_width(label) + 2, 4, label)
            pdf.set_font("Helvetica", "I", 7)
            pdf.set_text_color(*MUTED)
            pdf.cell(0, 4, pdf.t(f"{m['tool']} · {m['data_excluded_pct']:g}% of data excluded"),
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", size=7.5)
            pdf.multi_cell(self.W, 3.3, pdf.t(m["method"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1.4)


def build_report(recap: dict, stats: dict, highlights: list[dict], match: dict, overview: dict,
                 generated_at: datetime) -> _Report:
    """The laid-out report; its `printed` texts are what the tests read. `recap_pdf` gives its bytes."""
    return _Builder(recap, stats, highlights, match, overview, generated_at).build()


def recap_pdf(recap: dict, stats: dict, highlights: list[dict], match: dict, overview: dict,
              generated_at: datetime) -> bytes:
    """`recap` follows `Recap`, `stats` `MatchStats` of the same ticket and version, `highlights` the Recap's
    `Highlight`s in the order of its `highlight_ids`, `match` `MatchHeader`, `overview` `MatchOverview` (names, short
    names and colours)."""
    return bytes(build_report(recap, stats, highlights, match, overview, generated_at).output())
