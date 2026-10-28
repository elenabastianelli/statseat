"""Renders the Kit Man knowledge base for one match as Markdown documents.

Every fact comes from the validated universe and match information, so the
documents cannot contradict the data the app shows. Each document starts with
a small front matter block (doc_id, match_id, title) used by the retriever to
keep Kit Man inside the match perimeter. Sections are short and start with a
heading, so each one can be indexed as its own chunk.

Positions always use the same English label (schema.POSITION_LABELS) with the
code in brackets where useful. The documents only cover the match of the
ticket: there is no other game of the same matchday anywhere.
"""

from __future__ import annotations

from datetime import date

from .careers import season_start
from .schema import POSITION_LABELS, REFEREE_ROLE_LABELS, STAFF_ROLE_LABELS, MatchInfo, Player, Universe
from .scout import CONTENT_DIR, age_on, format_eur, load_content, ordinal

MOVE_LABELS = {"academy": "academy graduate", "transfer": "transfer", "free_transfer": "free transfer",
               "loan": "loan", "loan_return": "back from loan"}


OUT_REASONS = {"injury": "injured", "suspension": "suspended", "illness": "ill", "coach_decision": "coach's decision"}

def _front(doc_id: str, match_id: str, title: str) -> str:
    # The title is quoted: unquoted, "Matchday: A v B" is not valid YAML.
    return f"---\ndoc_id: {doc_id}\nmatch_id: {match_id}\ntitle: \"{title}\"\nsource: synthetic universe (Scout v1)\n---\n\n"


def name(p) -> str:
    """Display name: title, first name, nickname in quotes for staff, last name."""
    title = f"{p.title} " if p.title else ""
    return f"{title}{p.first_name} {p.last_name}"


def _n(k: int, word: str) -> str:
    plural = {"penalty": "penalties"}.get(word, word + "s")
    return f"{k} {word if k == 1 else plural}"


def _date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def position(code: str) -> str:
    return f"{POSITION_LABELS[code]} ({code})"


def render_docs(u: Universe, m: MatchInfo) -> dict[str, str]:
    clubs = {c.id: c for c in u.clubs}
    details = {d.club_id: d for d in u.club_details}
    players = {p.id: p for p in u.players}
    everyone = players | {lp.id: lp for lp in u.league_players}
    officials = {o.id: o for o in u.officials}
    staff = {s.id: s for s in u.staff}
    coaches = {c.id: c for c in u.coaches} | staff
    stadium = next(s for s in u.stadiums if s.id == m.environment.stadium_id)
    kickoff_day = date.fromisoformat(m.planned_kickoff[:10])
    home, guest = clubs[m.home_team_id], clubs[m.guest_team_id]
    comp = next(c for c in u.competitions if c.id == m.competition_id)
    table = next(s for s in u.standings if s.competition_id == m.competition_id)
    rows = {r.club_id: r for r in table.rows}
    h2h = next(h for h in u.head_to_head if {h.club_a, h.club_b} == {home.id, guest.id})
    panel = load_content()["match"]["referee_panel_average"]
    teams = {t.team_id: t for t in m.teams}
    docs: dict[str, str] = {}

    def club_name(cid: str) -> str:
        return clubs[cid].name

    def scorers_text(goals) -> str:
        return ", ".join(f"{name(players[g.player_id])} {g.minute}'{' (pen.)' if g.penalty else ''}" for g in goals)

    # --- Matchday ---
    w = m.environment.weather
    L = [_front("matchday", m.match_id, f"Matchday: {home.name} v {guest.name}"),
         f"# {home.name} v {guest.name}\n", "## The match\n",
         f"- Competition: {comp.name}, season {m.season}, matchday {m.matchday}.",
         f"- Kick-off: {m.planned_kickoff[11:16]} local time, {kickoff_day:%A} {_date(kickoff_day)}.",
         f"- Venue: {stadium.name}, {stadium.town} (capacity {stadium.capacity:,}). Tickets sold: {m.environment.tickets_sold:,}.",
         f"- Home team: {home.name} ({home.nickname}), in their home kit: {details[home.id].kits['home'].description}",
         f"- Away team: {guest.name} ({guest.nickname}), in their away kit: {details[guest.id].kits['away'].description}",
         f"- This is the local derby, known as {h2h.name}.",
         f"- Floodlights {m.environment.floodlight}. Roof: {m.environment.roof}.\n",
         "## Weather forecast\n",
         f"Conditions at kick-off: {w.temperature_c} °C, {'dry' if w.precipitation == 'none' else w.precipitation.replace('_', ' ')}, "
         f"{w.wind}, humidity {w.humidity_pct}%. The forecast below is all the weather information available.\n"]
    for f in m.environment.forecast:
        rain = "dry" if f.precipitation == "none" else f.precipitation.replace("_", " ")
        L.append(f"- {f.time} ({f.phase}): {f.temperature_c} °C, {f.sky}, {rain}, chance of rain {f.chance_of_rain_pct}%, {f.wind}.")
    L.append("\n## Pre-match programme\n")
    L += [f"- {p.time}: {p.item}" for p in u.match_preview.programme]
    L.append("\n## League position before kick-off\n")
    for c in (home, guest):
        r = rows[c.id]
        L.append(f"- {c.name}: {ordinal(r.position)} with {r.points} points from {r.played} games "
                 f"({_n(r.won, 'win')}, {_n(r.drawn, 'draw')}, {_n(r.lost, 'defeat')}; goals {r.goals_for}-{r.goals_against}). "
                 f"Form in the last five games (oldest first): {' '.join(r.form)}.")
    L.append("\n## Last five league games, with scorers\n")
    for c in (home, guest):
        L.append(f"### {c.name}\n")
        for g in details[c.id].season_matches[-5:]:
            opp = club_name(g.opponent_id)
            score = f"{c.name} {g.goals_for}-{g.goals_against} {opp}" if g.venue == "home" \
                else f"{opp} {g.goals_against}-{g.goals_for} {c.name}"
            L.append(f"- Matchday {g.round} ({g.venue}): {score}. {c.short_name} scorers: "
                     f"{scorers_text(g.scorers) if g.scorers else 'none'}.")
        L.append("")
    L.append("## Team news\n")
    for tn in u.match_preview.team_news:
        L.append(f"### {club_name(tn.club_id)}\n")
        L += [f"- {x}" for x in tn.items]
        L.append("")
    L.append("## Starting XI\n")
    for t in m.teams:
        L.append(f"### {club_name(t.team_id)}: starting XI ({t.formation})\n")
        for e in (e for e in t.lineup if e.starting):
            p = players[e.player_id]
            L.append(f"- #{e.shirt_number} {name(p)}, {POSITION_LABELS[e.playing_position]}{' (captain)' if e.captain else ''}")
        L.append("")
    L.append("## Substitutes\n")
    for t in m.teams:
        L.append(f"### {club_name(t.team_id)}: substitutes\n")
        for e in (e for e in t.lineup if not e.starting):
            L.append(f"- #{e.shirt_number} {name(players[e.player_id])}, {POSITION_LABELS[e.playing_position]}")
        L.append("")
    L.append("## Not in the matchday squad\n")
    reasons = OUT_REASONS
    for t in m.teams:
        out = [p for p in u.players if p.club_id == t.team_id and p.match_status == "not_selected"]
        L.append(f"### {club_name(t.team_id)}\n")
        for p in sorted(out, key=lambda p: p.shirt_number):
            extra = f" {p.availability.detail}" if p.availability.status != "available" else ""
            L.append(f"- #{p.shirt_number} {name(p)}, {POSITION_LABELS[p.position]}: {reasons[p.not_selected_reason]}.{extra}")
        L.append("")
    L.append("## Penalties and set pieces today\n")
    L.append("The club's order of preference, keeping only the players in today's matchday squad.\n")
    for t in m.teams:
        d = details[t.team_id]
        status = {e.player_id: "starting" if e.starting else "on the bench" for e in t.lineup}
        L.append(f"### {club_name(t.team_id)}\n")
        for label, ids in (("Penalties", d.set_pieces.penalties), ("Free kicks", d.set_pieces.free_kicks),
                           ("Corners", d.set_pieces.corners)):
            today_ids = [i for i in ids if i in status]
            line = f"- {label}: " + (", then ".join(f"{name(players[i])} ({status[i]})" for i in today_ids)
                                     if today_ids else "none of the usual takers is in the matchday squad") + "."
            out = [players[i] for i in ids if i not in status]
            if out:
                line += " Not available today: " + ", ".join(
                    f"{name(p)} ({OUT_REASONS.get(p.not_selected_reason, 'not selected')})" for p in out) + "."
            L.append(line)
        L.append("")
    L.append("\n## Form of the starting players (last five league games)\n")
    for t in m.teams:
        L.append(f"### {club_name(t.team_id)}\n")
        for e in (e for e in t.lineup if e.starting):
            p = players[e.player_id]
            g, a = sum(f.goals for f in p.form), sum(f.assists for f in p.form)
            starts, mins = sum(f.started for f in p.form), sum(f.minutes for f in p.form)
            L.append(f"- #{p.shirt_number} {name(p)}: {_n(starts, 'start')}, {mins} minutes, {_n(g, 'goal')}, {_n(a, 'assist')}.")
        L.append("")
    L.append("## Key duels\n")
    for kd in u.match_preview.key_duels:
        L.append(f"- {kd.title}: {kd.note}")
    lm = h2h.last_meeting
    L += [f"\n## {h2h.name}: head-to-head\n", h2h.story, "",
          f"- All-time record: {h2h.all_time['played']} matches; {home.short_name} won {h2h.all_time[f'{home.id}_wins']}, "
          f"{guest.short_name} won {h2h.all_time[f'{guest.id}_wins']}, {h2h.all_time['draws']} draws.",
          "- Last six league meetings (oldest first):"]
    L += [f"  - {mt.season}: {club_name(mt.home_id)} {mt.home_goals}-{mt.away_goals} {club_name(mt.away_id)}"
          for mt in h2h.last_meetings]
    L += [f"- The last meeting: {lm.summary}", "\n## Head coaches\n"]
    for c in (home, guest):
        hc = details[c.id].head_coach
        L.append(f"- {c.name}: {name(staff[hc.staff_id])}. Job security: {hc.job_security.level.replace('_', ' ')}. "
                 f"{hc.job_security.summary}")
    L.append("\n## Match officials\n")
    for mo in m.officials:
        L.append(f"- {REFEREE_ROLE_LABELS[mo.role]}: {name(officials[mo.official_id])}")
    L.append("\nMore about the officials, including cards per game, is in the officials document.\n")
    docs["matchday.md"] = "\n".join(L)

    # --- Stadium ---
    L = [_front("stadium", m.match_id, stadium.name), f"# {stadium.name}\n", "## Key facts\n",
         f"- Home of {club_name(stadium.club_id)}. Address: {stadium.address}.",
         f"- Opened in {stadium.opened}. Capacity {stadium.capacity:,}. Record attendance {stadium.record_attendance.value:,} "
         f"({stadium.record_attendance.match}, {stadium.record_attendance.season}).",
         f"- Pitch: {stadium.pitch.length_m:g} x {stadium.pitch.width_m:g} m, {stadium.pitch.surface}"
         f"{', with undersoil heating' if stadium.pitch.undersoil_heating else ''}.",
         f"- Roof: {stadium.roof}. Floodlights: {stadium.floodlights}. Big screens: {', '.join(stadium.big_screens)}.",
         f"- {stadium.history}\n", "## History: memorable matches here\n"]
    L += [f"- {x}" for x in stadium.notable_matches]
    L.append("\n## Stands and sectors\n")
    for st in stadium.stands:
        cap = sum(s.capacity for s in st.sectors)
        kinds = ", ".join(f"{s.id} ({s.type.replace('_', ' ')})" for s in st.sectors)
        L.append(f"### {st.name} ({st.side} side, {cap:,} seats)\n\n{st.description}\n\nSectors: {kinds}.\n")
    L.append("## Gates\n")
    for g in stadium.gates:
        stand = next(s for s in stadium.stands if s.id == g.stand_id)
        L.append(f"- Gate {g.id} ({stand.name}): sectors {', '.join(g.serves)}.{' ' + g.note if g.note else ''}")
    L.append("\n## Services and where to find them\n")
    for sv in stadium.services:
        L.append(f"- {sv.name}: near sectors {', '.join(sv.near)}.{' ' + sv.offer[0].upper() + sv.offer[1:] + '.' if sv.offer else ''}")
    L.append("\n## Getting there\n")
    L += [f"- {t.mode.capitalize()}: {t.detail}" for t in stadium.transport]
    L.append("\n## Stadium rules\n")
    L += [f"- {p}" for p in stadium.policies]
    docs["stadium.md"] = "\n".join(L) + "\n"

    # --- Clubs and squads ---
    for c in (home, guest):
        d = details[c.id]
        r = rows[c.id]
        club_staff = [s for s in u.staff if s.club_id == c.id]
        hc = staff[d.head_coach.staff_id]
        squad = sorted((p for p in u.players if p.club_id == c.id), key=lambda p: p.shirt_number)
        captain = next(p for p in squad if p.captaincy == "captain")
        vice = next(p for p in squad if p.captaincy == "vice_captain")
        L = [_front(f"club-{c.id}", m.match_id, c.name), f"# {c.name}\n", "## Club profile\n",
             f"- Nickname: {c.nickname}. Founded in {c.founded} in {c.town}.",
             f"- Colours: {d.colour_names}. Home kit: {d.kits['home'].description} Away kit: {d.kits['away'].description}",
             f"- Crest: {d.crest}",
             f"- Stadium: {c.stadium.name} ({c.stadium.capacity:,} seats).",
             f"- Chairman: {name(d.chairman)}, since {d.chairman.since}. {d.chairman.bio}",
             f"- Competition: {comp.name}. Last season: {ordinal(d.last_season.position)}. "
             f"Now: {ordinal(r.position)} after {r.played} games, {r.points} points.",
             f"- Main rival: {club_name(c.rival_id)} ({h2h.name})." if c.rival_id else "- No main rival.",
             f"- Club captain: {name(captain)} (#{captain.shirt_number}). Vice-captain: {name(vice)} (#{vice.shirt_number}).",
             f"- Formation: {d.formation}. Playing style: {d.playing_style}\n", "## History\n"]
        L += [f"{para}\n" for para in d.history]
        L.append("## Honours\n")
        for h in d.honours:
            L.append(f"- {h.competition} ({len(h.seasons)}): {', '.join(h.seasons)}. Most recent: {h.seasons[-1]}.")
        L += ["\n## Club records\n", f"- Biggest win: {d.records.biggest_win}",
              f"- All-time top scorer: {d.records.top_scorer}", f"- Most appearances: {d.records.most_appearances}",
              f"- Record attendance at home: see the stadium document." if c.id == stadium.club_id else
              f"- Home ground capacity: {c.stadium.capacity:,}.", "\n## Club legends\n"]
        L += [f"- {name(lg)}, {lg.role}: {lg.fact}" for lg in d.legends]
        L.append("\n## Traditions\n")
        L += [f"- {t}" for t in d.traditions]
        L += [f"\n## Head coach: {name(hc)}\n",
              f"- {hc.nationality}, born {hc.born}, head coach since {hc.since}. {hc.bio}",
              f"- Tactical style: {d.head_coach.tactical_style}",
              "- Career:"]
        L += [f"  - {x}" for x in d.head_coach.career]
        L.append(f"- Job security: {d.head_coach.job_security.level.replace('_', ' ')}. {d.head_coach.job_security.summary}\n")
        L.append("## Staff\n")
        for s in club_staff:
            nick = f' Known as "{s.nickname}".' if s.nickname else ""
            L.append(f"### {STAFF_ROLE_LABELS[s.role]}: {name(s)}\n\n{s.nationality}, born {s.born}, at the club since "
                     f"{s.since}. {s.bio}{nick}\n")
        L.append("## Set-piece takers (order of preference)\n")
        L.append("Today's takers, without the players who are out, are in the matchday document.\n")
        for label, ids in (("Penalties", d.set_pieces.penalties), ("Free kicks", d.set_pieces.free_kicks),
                           ("Corners", d.set_pieces.corners)):
            L.append(f"- {label}: " + ", then ".join(f"{name(players[i])} (#{players[i].shirt_number}{out_today(players[i])})"
                                                    for i in ids) + ".")
        L.append("\n## This season, game by game\n")
        for g in d.season_matches:
            opp = club_name(g.opponent_id)
            score = f"{c.name} {g.goals_for}-{g.goals_against} {opp}" if g.venue == "home" \
                else f"{opp} {g.goals_against}-{g.goals_for} {c.name}"
            L.append(f"- Matchday {g.round}: {score}. Scorers: {scorers_text(g.scorers) if g.scorers else 'none'}.")
        docs[f"club-{c.id}.md"] = "\n".join(L) + "\n"

        team = teams[c.id]
        entry = {e.player_id: e for e in team.lineup}
        roles = {pid: [label for label, ids in (("penalties", d.set_pieces.penalties), ("free kicks", d.set_pieces.free_kicks),
                                                ("corners", d.set_pieces.corners)) if pid in ids]
                 for pid in players}
        L = [_front(f"squad-{c.id}", m.match_id, f"{c.name} squad"), f"# {c.name}: squad {u.meta.season}\n",
             "| # | Player | Position | Age | Nationality | Today |", "|---|---|---|---|---|---|"]
        for p in squad:
            L.append(f"| {p.shirt_number} | {name(p)} | {POSITION_LABELS[p.position]} | {age_on(p.date_of_birth, kickoff_day)} | "
                     f"{p.nationality} | {today(p, entry)} |")
        L.append("")
        for p in squad:
            L += player_section(p, c, comp.name, kickoff_day, entry, roles[p.id], d, clubs, coaches, u)
        docs[f"squad-{c.id}.md"] = "\n".join(L)

    # --- Officials ---
    L = [_front("officials", m.match_id, "Match officials"), "# Match officials\n",
         f"For reference, referees on the national panel showed on average {panel['yellow_cards_per_match']} yellow "
         f"cards and {panel['red_cards_per_match']} red cards per game last season.\n"]
    for mo in m.officials:
        o = officials[mo.official_id]
        L.append(f"## {REFEREE_ROLE_LABELS[mo.role]}: {name(o)}\n")
        L.append(f"- {o.nationality}, born {o.born} in {o.birthplace.town}. On the national panel since {o.panel_since} "
                 f"as {'a referee' if o.specialism == 'referee' else 'an assistant referee'}; "
                 f"{o.top_flight_matches} top-division games.")
        L.append(f"- {o.experience}")
        if o.career_cards:
            cc, sc = o.career_cards, o.season_cards
            L.append(f"- As referee in the top division: {cc.matches} games, {cc.yellow_cards} yellow cards "
                     f"({cc.yellow_cards / cc.matches:.1f} per game), {cc.red_cards} red cards "
                     f"({cc.red_cards / cc.matches:.2f} per game), {cc.penalties_awarded} penalties awarded.")
            L.append(f"- This season: {_n(sc.matches, 'game')}, {_n(sc.yellow_cards, 'yellow card')} "
                     f"({sc.yellow_cards / sc.matches:.1f} per game), {_n(sc.red_cards, 'red card')}, "
                     f"{_n(sc.penalties_awarded, 'penalty')} awarded.")
        L.append("")
    docs["officials.md"] = "\n".join(L)

    # --- League ---
    L = [_front("league", m.match_id, "The league"), f"# Football in {u.meta.country}\n", u.meta.country_note + "\n"]
    current_coach = {h.club_id: h.coach_id for h in u.coaching_history if h.to_season is None}
    for cp in u.competitions:
        st = next(s for s in u.standings if s.competition_id == cp.id)
        L += [f"## {cp.name} (tier {cp.tier}, {cp.clubs} clubs): table after matchday {st.after_round}\n",
              "| Pos | Club | P | W | D | L | GF | GA | GD | Pts | Form |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {r.position} | {club_name(r.club_id)} | {r.played} | {r.won} | {r.drawn} | {r.lost} | {r.goals_for} | "
              f"{r.goals_against} | {r.goal_difference:+d} | {r.points} | {r.form} |" for r in st.rows]
        ts = next((t for t in u.top_scorers if t.competition_id == cp.id), None)
        if ts:
            L += [f"\n### {cp.name}: top scorers after matchday {ts.after_round}\n", "| Rank | Player | Club | Goals |",
                  "|---|---|---|---|"]
            L += [f"| {r.rank} | {name(everyone[r.player_id])} | {club_name(r.club_id)} | {r.goals} |" for r in ts.rows]
        L.append(f"\n### {cp.name}: results\n")
        for rnd in sorted({r.round for r in u.results if r.competition_id == cp.id}):
            games = [r for r in u.results if r.competition_id == cp.id and r.round == rnd]
            L.append(f"- Matchday {rnd}: " + "; ".join(f"{club_name(r.home_id)} {r.home_goals}-{r.away_goals} "
                                                       f"{club_name(r.away_id)}" for r in games) + ".")
        nxt = [f for f in u.fixtures if f.competition_id == cp.id]
        if nxt:
            L.append(f"\n### {cp.name}: next round (matchday {nxt[0].round})\n")
            L += [f"- {club_name(f.home_id)} v {club_name(f.away_id)}" for f in nxt]
        L.append(f"\n### Clubs in the {cp.name}\n")
        L += [f"- **{c.name}** ({c.nickname}, {c.town}, founded {c.founded}; {c.stadium.name}, {c.stadium.capacity:,}; "
              f"head coach {name(coaches[current_coach[c.id]])}): {c.summary}"
              for c in sorted(u.clubs, key=lambda c: c.name) if c.competition_id == cp.id]
        L.append("")
    L += [f"## Roll of honour since {u.roll_of_honour[0].season}\n",
          "| Season | Meridian League | Meridian Cup | Meridian Championship | Super Shield (August) |", "|---|---|---|---|---|"]
    L += [f"| {r.season} | {club_name(r.league)} | {club_name(r.cup)} | {club_name(r.championship)} | "
          f"{club_name(r.super_shield)} |" for r in u.roll_of_honour]
    docs["league.md"] = "\n".join(L) + "\n"

    # --- Rules (hand-written, the same for every match) ---
    docs["rules.md"] = _front("rules", m.match_id, "Rules of the game") + (CONTENT_DIR / "rules.md").read_text(encoding="utf-8")
    return docs


def out_today(p: Player) -> str:
    """', out today: ill' for a player who is not in today's matchday squad, else ''."""
    if p.match_status != "not_selected":
        return ""
    return f", out today: {OUT_REASONS.get(p.not_selected_reason, 'not selected')}"


def today(p: Player, entry: dict) -> str:
    e = entry.get(p.id)
    if e is None:
        reason = {"injury": "injured", "suspension": "suspended", "illness": "ill", "coach_decision": "coach's decision"}
        return f"not in the matchday squad ({reason[p.not_selected_reason]})"
    if e.starting:
        return f"starting, {POSITION_LABELS[e.playing_position]}{', captain' if e.captain else ''}"
    return "substitute"


def player_section(p: Player, c, comp_name: str, kickoff_day: date, entry: dict, set_roles: list[str], d, clubs: dict,
                   coaches: dict, u: Universe) -> list[str]:
    age = age_on(p.date_of_birth, kickoff_day)
    ts = p.this_season
    cap = {"captain": " Club captain.", "vice_captain": " Vice-captain.", "none": ""}[p.captaincy]
    intl = (f"{_n(p.international.caps, 'senior cap')} and {_n(p.international.goals, 'goal')} for {p.international.team}."
            if p.international.caps else f"No senior caps for {p.international.team} yet.")
    lines = [f"## #{p.shirt_number} {name(p)}\n",
             f"- Position: {position(p.position)}.{cap} Today: {today(p, entry)}.",
             f"- Nickname: \"{p.nickname}\". {p.nickname_origin}",
             f"- Born {_date(p.date_of_birth)} in {p.birthplace.town}, {p.birthplace.country} (age {age}). Nationality: "
             f"{p.nationality}. Height {p.height_cm} cm, {p.preferred_foot}-footed.",
             f"- International: {intl}",
             f"- Playing style: {p.playing_style} Strengths: {', '.join(p.strengths)}."]
    if set_roles:
        lines.append(f"- Set pieces: on the list of takers for {', '.join(set_roles)} (see the club document for the order).")
    lines += [f"- This season in the {comp_name}: {_n(ts.appearances, 'appearance')} ({ts.starts} as a starter), "
              f"{ts.minutes} minutes, {_n(ts.goals, 'goal')}, {_n(ts.assists, 'assist')}, {_n(ts.yellow_cards, 'yellow card')}, "
              f"{_n(ts.red_cards, 'red card')}.",
              "- Last five league games: " + "; ".join(
                  f"matchday {f.round} v {clubs[f.opponent_id].short_name}: "
                  + ("did not play" if f.minutes == 0 else f"{'started' if f.started else 'came on'}, {f.minutes}'"
                     + (f", {_n(f.goals, 'goal')}" if f.goals else "") + (f", {_n(f.assists, 'assist')}" if f.assists else ""))
                  for f in p.form) + ".",
              f"- Availability: {p.availability.detail}"
              + (f" Expected back: {p.availability.expected_return}." if p.availability.expected_return else "")]
    if p.injury_history:
        lines.append("- Injury record: " + " ".join(f"{i.season}: {i.injury}, {_n(i.matches_missed, 'league game')} "
                                                     f"missed. {i.note}" for i in p.injury_history))
    if p.honours:
        lines.append("- Honours: " + "; ".join(f"{h.competition} {h.season} ({clubs[h.club_id].name})" for h in p.honours) + ".")
    else:
        lines.append("- Honours: no major trophies yet.")
    lines.append("- Curiosities: " + " ".join(p.curiosities))
    cur = p.career[-1]
    if p.academy_graduate:
        joined = "Came through the club academy."
    elif cur.move == "transfer":
        joined = (f"Joined from {clubs[p.career[-2].club_id].name} in {cur.from_season} for {format_eur(cur.fee_eur)}.")
    else:
        joined = f"Joined on a free transfer from {clubs[p.career[-2].club_id].name} in {cur.from_season}."
    lines += [f"- {joined}", "- Career (league appearances and goals, including this season; head coaches at the time):"]
    for sp in p.career:
        period = f"{sp.from_season}–{sp.to_season or 'present'}" if sp.to_season != sp.from_season else sp.from_season
        how = MOVE_LABELS[sp.move]
        if sp.move == "transfer":
            how += f", {format_eur(sp.fee_eur)}"
        elif sp.move == "loan":
            how += f" from {clubs[sp.parent_club_id].name}, no fee"
        elif sp.move in ("free_transfer", "loan_return", "academy"):
            how += ", no fee"
        heads = ", ".join(name(coaches[x]) for x in sp.head_coach_ids)
        lines.append(f"  - {period}: {clubs[sp.club_id].name} ({how}), {_n(sp.appearances, 'appearance')}, "
                     f"{_n(sp.goals, 'goal')}; head coach{'es' if len(sp.head_coach_ids) > 1 else ''}: {heads}")
        if len(sp.seasons) > 1:
            lines += [f"    - {s.season}: {_n(s.appearances, 'appearance')}, {_n(s.goals, 'goal')}" for s in sp.seasons]
    lines.append("")
    return lines
