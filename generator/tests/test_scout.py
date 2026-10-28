"""Tests for Scout v1: determinism, scheduling, validation rules and Kit Man documents.

The universe is synthetic, so it is itself the fixture; each validator rule is tested by
breaking one fact in a copy and checking that the rule fails. The Tiki-Taka-Toe counting is
tested on a tiny hand-made squad with a hand-computed answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from insidethegame_gen import games
from insidethegame_gen.__main__ import dumps, main
from insidethegame_gen.attributes import ATTRIBUTES, SCALES_PATH, floors_for, load_scales, physical_value, scale_table
from insidethegame_gen.crests import REPO_ROOT
from insidethegame_gen.crests import build as draw_crests
from insidethegame_gen.kitman_docs import render_docs
from insidethegame_gen.match.calibration import load_calibration
from insidethegame_gen.overview import render_overview
from insidethegame_gen.schema import POSITION_LABELS, MatchInfo
from insidethegame_gen.scout import age_on, assign_home_away, build, format_eur, load_content, round_robin, season_label
from insidethegame_gen.season import FORMATIONS
from insidethegame_gen.validate import (
    check_attributes,
    check_blocklist,
    check_careers,
    check_coaching_history,
    check_content_safety,
    check_documents,
    check_games,
    check_head_to_head,
    check_honours,
    check_lineups,
    check_match,
    check_name_schema,
    check_names,
    check_no_concurrent_matches,
    check_results_and_standings,
    check_season_numbers,
    check_squad_lineup_alignment,
    check_stadium,
    check_top_scorers,
    run_all,
)


@pytest.fixture(scope="module")
def built():
    universe, match = build(2026)
    return universe, match, render_docs(universe, match), load_content()["blocklist"]


def copy(model):
    return model.model_copy(deep=True)


def team(match, club_id):
    return next(t for t in match.teams if t.team_id == club_id)


# --- Helpers ----------------------------------------------------------------


def test_season_label_and_age():
    assert season_label(2026) == "2026/27"
    assert season_label(1999) == "1999/00"
    assert age_on(date(2000, 10, 25), date(2026, 10, 24)) == 25
    assert age_on(date(2000, 10, 24), date(2026, 10, 24)) == 26


def test_round_robin_every_pair_once_and_balanced_home_games():
    clubs = [f"C{i:02d}" for i in range(18)]
    rounds = assign_home_away(round_robin(clubs))
    assert len(rounds) == 17
    for pairs in rounds:
        assert sorted(t for p in pairs for t in p) == clubs
    pairs = Counter(frozenset(p) for r in rounds for p in r)
    assert len(pairs) == 18 * 17 // 2 and set(pairs.values()) == {1}
    homes = Counter(h for r in rounds[:8] for h, _ in r)
    assert max(homes.values()) - min(homes.get(c, 0) for c in clubs) <= 2


def test_fee_formatting():
    assert format_eur(0) == "€0"
    assert format_eur(250_000) == "€250k"
    assert format_eur(4_500_000) == "€4.5m"
    assert format_eur(11_000_000) == "€11m"


# --- The generated universe -------------------------------------------------


def test_same_seed_same_output_and_other_seed_differs():
    a, ma = build(7)
    b, mb = build(7)
    c, _ = build(8)
    assert a.model_dump_json() == b.model_dump_json()
    assert ma.model_dump_json() == mb.model_dump_json()
    assert a.model_dump_json() != c.model_dump_json()


def test_two_runs_write_identical_files(tmp_path):
    hashes = []
    for run in ("a", "b"):
        out = tmp_path / run
        assert main(["--out", str(out)]) == 0
        hashes.append({str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(out.rglob("*")) if p.is_file()})
    assert hashes[0] == hashes[1] and len(hashes[0]) == 15


def test_a_failing_check_writes_nothing(tmp_path, monkeypatch):
    import insidethegame_gen.__main__ as cli
    from insidethegame_gen.validate import CheckResult

    monkeypatch.setattr(cli, "run_all", lambda *a, **k: [CheckResult("Broken on purpose", "fail", "test")])
    assert cli.main(["--out", str(tmp_path)]) == 1
    assert not any(tmp_path.rglob("*"))


def test_all_checks_pass(built):
    universe, match, docs, blocklist = built
    results = {r.name: r.status for r in run_all(universe, match, blocklist, docs)}
    assert results["Attributes 1-99"] == "pass"
    assert set(results.values()) == {"pass"}


def test_derby_is_on_matchday_nine_with_brackwater_at_home(built):
    universe, match, *_ = built
    assert match.matchday == 9 and (match.home_team_id, match.guest_team_id) == ("BRA", "CAL")
    assert not any(r.round >= 9 for r in universe.results)
    homes = Counter(r.home_id for r in universe.results if "BRA" in (r.home_id, r.away_id))
    assert 3 <= homes["BRA"] <= 5


def test_no_delete_events_no_result_and_english_codes_only(built):
    universe, match, *_ = built
    assert match.result is None
    text = match.model_dump_json()
    assert "Delete" not in text
    for dfl in ("TW", "IVR", "IVL", "RV", "LV", "DML", "DMR", "ZO", "OLM", "ORM", "STZ", "HL", "HR", "STL", "STR"):
        assert f'"{dfl}"' not in text
    assert {o.role for o in match.officials} == {"referee", "assistant_referee_1", "assistant_referee_2",
                                                 "fourth_official", "video_assistant_referee",
                                                 "assistant_video_assistant_referee"}
    assert all(e.playing_position in POSITION_LABELS for t in match.teams for e in t.lineup)


# --- Attributes 1-99 (N-4b) ---------------------------------------------------------


def test_every_player_has_six_calibrated_attributes(built):
    universe, *_ = built
    for p in universe.players:
        assert p.attributes_status == "calibrated"
        values = p.attributes.model_dump()
        assert list(values) == list(ATTRIBUTES)
        assert all(isinstance(v, int) and 1 <= v <= 99 for v in values.values())


def test_attributes_follow_the_profile(built):
    universe, *_ = built
    pacy = [p for p in universe.players if p.attribute_profile.style[:1] == ["pacy"]]
    assert pacy and all(p.attributes.pace >= 70 for p in pacy)
    for p in universe.players:
        for a, (floor, _) in floors_for(p.position_group, p.attribute_profile.style, p.strengths).items():
            assert getattr(p.attributes, a) >= floor
    # better players have better numbers on average
    starters = [p for p in universe.players if p.attribute_profile.tier == "starter"]
    prospects = [p for p in universe.players if p.attribute_profile.tier == "prospect"]
    mean = lambda ps: sum(sum(p.attributes.model_dump().values()) for p in ps) / len(ps)  # noqa: E731
    assert mean(starters) > mean(prospects) + 30


def test_attribute_mapping_agrees_with_the_match_generator(built):
    """Groups with their own DFL table: the documented mapping and match/calibration.py give the same top speed."""
    universe, *_ = built
    scales = load_scales()
    cal = load_calibration(SCALES_PATH.parent)
    for p in universe.players:
        if scale_table(scales, "pace", p.position_group)[1] == p.position_group:
            assert physical_value(scales, "pace", p.position_group, p.attributes.pace) == pytest.approx(
                cal.top_speed_for_pace(p.position_group, p.attributes.pace), abs=1e-6)
    assert physical_value(scales, "pace", "W", 90) == pytest.approx(
        scales["attributes"]["pace"]["by_role_group"]["W"]["values"][89])


@pytest.mark.parametrize("breaker", ["none", "zero", "pending", "promise"])
def test_attribute_check_fails_when_broken(built, breaker):
    universe, *_ = built
    u = copy(universe)
    p = next(p for p in u.players if p.attribute_profile.style[:1] == ["pacy"])
    if breaker == "none":
        p.attributes.stamina = None
    elif breaker == "zero":
        p.attributes.passing = 0
    elif breaker == "pending":
        p.attributes_status = "pending_calibration"
    else:
        p.attributes.pace = 40  # his style "pacy" promises at least 70
    assert check_attributes(u).status == "fail"


def test_attribute_check_fails_without_a_usable_calibration(built):
    universe, *_ = built
    scales = load_scales()
    no_gk = json.loads(json.dumps(scales))
    no_gk["attributes"]["pace"]["by_role_group"]["GK"] = None  # goalkeepers never borrow a table
    assert check_attributes(universe, no_gk).status == "fail"
    too_fast = json.loads(json.dumps(scales))
    too_fast["attributes"]["pace"]["by_role_group"]["W"]["values"] = [30.0 + i * 0.1 for i in range(99)]  # p99 39.8
    assert check_attributes(universe, too_fast).status == "fail"
    no_source = json.loads(json.dumps(scales))
    no_source["meta"] = {}
    assert check_attributes(universe, no_source).status == "fail"


def test_fees_are_euros_and_zero_unless_transfer(built):
    universe, *_ = built
    for p in universe.players:
        for sp in p.career:
            assert (sp.fee_eur > 0) == (sp.move == "transfer")
    assert universe.meta.currency == "EUR"


def test_match_info_keeps_dfl_structure_with_staff(built):
    _, match, *_ = built
    data = json.loads(match.model_dump_json())
    assert set(data) >= {"match_id", "environment", "teams", "officials"}
    for t in data["teams"]:
        assert set(t) == {"role", "team_id", "formation", "kit", "crest_file", "kit_files", "lineup", "staff"}
        assert any(s["role"] == "head_coach" for s in t["staff"])


# --- Pre-match data in the match information (task E-6) -------------------------------


def test_match_info_has_capacity_tickets_crest_and_kits(built):
    """The stadium capacity sits next to the tickets sold; each team cites its crest and both kits."""
    universe, match, *_ = built
    env = match.environment
    assert env.stadium_capacity == universe.stadiums[0].capacity == 60400
    assert env.tickets_sold == 60212 <= env.stadium_capacity
    for t in match.teams:
        assert t.crest_file == f"synthetic/crests/{t.team_id}.svg"
        assert t.kit_files.model_dump() == {s: f"synthetic/kits/{t.team_id}_{s}.svg" for s in ("home", "away")}
        assert t.kit in t.kit_files.model_dump()  # the kit worn today is one of the two files
        for path in (t.crest_file, t.kit_files.home, t.kit_files.away):
            assert (REPO_ROOT / path).is_file(), path


def test_cited_crest_and_kit_files_are_the_ones_the_crest_generator_draws(built):
    universe, match, *_ = built
    drawn = {f"synthetic/{rel}" for rel in draw_crests(universe.model_dump(mode="json"))[0]}
    for t in match.teams:
        assert {t.crest_file, t.kit_files.home, t.kit_files.away} <= drawn


def test_committed_match_info_is_up_to_date(built):
    _, match, *_ = built
    committed = (REPO_ROOT / "synthetic" / match.match_id / "match_info.json").read_text(encoding="utf-8")
    assert committed == dumps(match.model_dump(mode="json")), "run: cd generator && .venv/bin/python -m insidethegame_gen"


def test_match_info_schema_requires_capacity_crest_and_kits():
    defs = MatchInfo.model_json_schema()["$defs"]
    assert "stadium_capacity" in defs["Environment"]["required"]
    assert {"crest_file", "kit_files"} <= set(defs["MatchTeam"]["required"])
    assert set(defs["KitFiles"]["required"]) == {"home", "away"}


def test_capacity_and_tickets_checks_fail_when_broken(built):
    universe, match, *_ = built
    assert check_stadium(universe, match).status == "pass"
    m = copy(match)
    m.environment.stadium_capacity = 38200  # the guest club's ground, not the venue
    assert check_stadium(universe, m).status == "fail"
    m = copy(match)
    m.environment.tickets_sold = m.environment.stadium_capacity + 1
    assert check_stadium(universe, m).status == "fail"


def test_crest_and_kit_file_checks_fail_when_broken(built, tmp_path):
    universe, match, *_ = built
    assert check_match(universe, match).status == "pass"
    m = copy(match)
    m.teams[0].crest_file = "synthetic/crests/CAL.svg"  # the other club's crest
    assert check_match(universe, m).status == "fail"
    m = copy(match)
    m.teams[1].kit_files.away = "synthetic/kits/CAL_third.svg"
    assert check_match(universe, m).status == "fail"
    assert check_match(universe, match, root=tmp_path).status == "fail"  # files not drawn under that root


def test_no_other_game_of_matchday_nine(built):
    universe, match, docs, _ = built
    assert all(f.round == 10 for f in universe.fixtures)
    assert all(r.round <= 8 for r in universe.results)


# --- Names (UNIVERSO-SPEC 1.1) ------------------------------------------------


def test_surname_shared_by_official_and_player_fails(built):
    universe, match, *_ = built
    u = copy(universe)
    u.officials[1].last_name = u.players[5].last_name
    assert check_names(u, match).status == "fail"


def test_surname_shared_by_two_coaches_fails(built):
    universe, match, *_ = built
    u = copy(universe)
    u.coaches[3].last_name = u.coaches[4].last_name
    assert check_names(u, match).status == "fail"


def test_repeated_first_name_in_the_match_fails(built):
    universe, match, *_ = built
    u = copy(universe)
    bra = [p for p in u.players if p.club_id == "BRA"]
    cal = [p for p in u.players if p.club_id == "CAL"]
    cal[3].first_name = bra[2].first_name
    assert check_names(u, match).status == "fail"


def test_coach_with_an_official_first_name_fails(built):
    universe, match, *_ = built
    u = copy(universe)
    u.coaches[0].first_name = u.officials[0].first_name
    assert check_names(u, match).status == "fail"


def test_duplicate_nickname_fails(built):
    universe, match, *_ = built
    u = copy(universe)
    u.players[1].nickname = u.players[0].nickname
    assert check_names(u, match).status == "fail"


def test_title_or_nickname_inside_the_name_fails(built):
    universe, *_ = built
    u = copy(universe)
    doctor = next(s for s in u.staff if s.title)
    doctor.first_name = f"{doctor.title} {doctor.first_name}"
    assert check_name_schema(u).status == "fail"
    u = copy(universe)
    kit = next(s for s in u.staff if s.nickname)
    kit.first_name = f"{kit.first_name} '{kit.nickname}'"
    assert check_name_schema(u).status == "fail"


def test_real_person_name_fails(built):
    universe, _, _, blocklist = built
    u = copy(universe)
    u.players[0].first_name, u.players[0].last_name = "Harry", "Kane"
    assert check_blocklist(u, blocklist).status == "fail"


def test_famous_surname_fails(built):
    universe, _, _, blocklist = built
    u = copy(universe)
    u.coaches[3].last_name = "Haaland"
    assert check_blocklist(u, blocklist).status == "fail"


# --- Line-ups and alignment (UNIVERSO-SPEC 1.2-1.4) -------------------------------


def test_null_or_dfl_position_fails(built):
    universe, match, *_ = built
    for bad in (None, "TW", "FB"):
        m = copy(match)
        team(m, "BRA").lineup[12].playing_position = bad
        assert check_lineups(universe, m).status == "fail"


def test_starters_must_match_the_formation_exactly(built):
    universe, match, *_ = built
    m = copy(match)
    starter = next(e for e in team(m, "CAL").lineup if e.starting and e.playing_position == "RS")
    starter.playing_position = "ST"
    assert check_lineups(universe, m).status == "fail"
    assert sorted(e.playing_position for e in team(match, "CAL").lineup if e.starting) == sorted(FORMATIONS["4-4-2"])


def test_eleven_starters_nine_substitutes_one_captain(built):
    universe, match, *_ = built
    m = copy(match)
    next(e for e in team(m, "BRA").lineup if e.starting and not e.captain).starting = False
    assert check_lineups(universe, m).status == "fail"
    m = copy(match)
    next(e for e in team(m, "BRA").lineup if e.starting and not e.captain).captain = True
    assert check_lineups(universe, m).status == "fail"


def test_duplicate_player_in_lineup_fails(built):
    universe, match, *_ = built
    m = copy(match)
    lineup = team(m, "BRA").lineup
    lineup[-1].player_id = lineup[-2].player_id
    assert check_lineups(universe, m).status == "fail"


def test_shirt_position_and_status_must_agree_with_the_squad(built):
    universe, match, *_ = built
    m = copy(match)
    team(m, "BRA").lineup[0].shirt_number = 77
    assert check_squad_lineup_alignment(universe, m).status == "fail"
    m = copy(match)
    sub = next(e for e in team(m, "CAL").lineup if not e.starting and e.playing_position != "GK")
    sub.playing_position = "GK"
    assert check_squad_lineup_alignment(universe, m).status == "fail"
    u = copy(universe)
    next(p for p in u.players if p.match_status == "substitute").match_status = "starting"
    assert check_squad_lineup_alignment(u, match).status == "fail"


def test_unavailable_player_cannot_be_selected(built):
    universe, match, *_ = built
    u = copy(universe)
    p = next(p for p in u.players if p.match_status == "starting")
    p.availability.status, p.availability.expected_return = "injured", "matchday 10"
    assert check_squad_lineup_alignment(u, match).status == "fail"


def test_not_selected_players_have_a_reason(built):
    universe, match, *_ = built
    out = [p for p in universe.players if p.match_status == "not_selected"]
    assert len(out) == 10 and all(p.not_selected_reason for p in out)
    u = copy(universe)
    next(p for p in u.players if p.match_status == "not_selected").not_selected_reason = None
    assert check_squad_lineup_alignment(u, match).status == "fail"


def test_armband_follows_captain_then_vice(built):
    universe, match, *_ = built
    for t in match.teams:
        squad = {p.id: p for p in universe.players if p.club_id == t.team_id}
        wearer = squad[next(e.player_id for e in t.lineup if e.captain)]
        starters = {e.player_id for e in t.lineup if e.starting}
        captain = next(p for p in squad.values() if p.captaincy == "captain")
        vice = next(p for p in squad.values() if p.captaincy == "vice_captain")
        assert wearer.id == (captain.id if captain.id in starters else vice.id)


# --- Careers, fees, honours, coaches ---------------------------------------------


def test_career_gap_fails(built):
    universe, *_ = built
    u = copy(universe)
    p = next(p for p in u.players if len(p.career) >= 2)
    first = p.career[0]
    first.from_season = season_label(int(first.from_season[:4]) - 2)
    first.to_season = season_label(int(p.career[1].from_season[:4]) - 2)
    assert check_careers(u).status == "fail"


def test_fee_rules_fail_when_broken(built):
    universe, *_ = built
    u = copy(universe)
    next(sp for p in u.players for sp in p.career if sp.move == "loan").fee_eur = 500_000
    assert check_careers(u).status == "fail"
    u = copy(universe)
    next(sp for p in u.players for sp in p.career if sp.move == "transfer").fee_eur = 0
    assert check_careers(u).status == "fail"


def test_academy_graduate_must_start_at_the_club(built):
    universe, *_ = built
    u = copy(universe)
    p = next(p for p in u.players if p.academy_graduate)
    p.academy_graduate = False
    assert check_careers(u).status == "fail"
    u = copy(universe)
    next(p for p in u.players if not p.academy_graduate).academy_graduate = True
    assert check_careers(u).status == "fail"


def test_loan_needs_its_parent_club(built):
    universe, *_ = built
    u = copy(universe)
    next(sp for p in u.players for sp in p.career if sp.move == "loan").parent_club_id = "RAV"
    assert check_careers(u).status == "fail"


def test_player_honours_follow_from_the_career(built):
    universe, *_ = built
    u = copy(universe)
    p = next(p for p in u.players if p.honours)
    p.honours.pop()
    assert check_honours(u).status == "fail"
    u = copy(universe)
    p = next(p for p in u.players if p.honours)
    p.honours.append(p.honours[0].model_copy(update={"season": "1999/00"}))  # no spell anywhere in 1999/00
    assert check_honours(u).status == "fail"


def test_club_honours_agree_with_the_roll(built):
    universe, *_ = built
    u = copy(universe)
    d = next(d for d in u.club_details if d.club_id == "CAL")
    next(h for h in d.honours if h.competition == "Meridian Cup").seasons.remove("2008/09")
    assert check_honours(u).status == "fail"


def test_coach_at_two_clubs_at_once_fails(built):
    universe, *_ = built
    u = copy(universe)
    current = [h for h in u.coaching_history if h.to_season is None and h.coach_id.startswith("COA-")]
    current[0].coach_id = current[1].coach_id
    assert check_coaching_history(u).status == "fail"


# --- Season numbers, head-to-head, top scorers, standings ----------------------------


def test_tampered_standings_fail(built):
    universe, *_ = built
    u = copy(universe)
    u.standings[0].rows[0].points += 1
    assert check_results_and_standings(u).status == "fail"


def test_player_goals_must_add_up_to_team_goals(built):
    universe, *_ = built
    u = copy(universe)
    next(p for p in u.players if p.club_id == "CAL").this_season.goals += 1
    assert check_season_numbers(u).status == "fail"


def test_minutes_cannot_exceed_ninety_per_appearance(built):
    universe, *_ = built
    u = copy(universe)
    p = next(p for p in u.players if p.this_season.appearances > 0)
    p.this_season.minutes = 90 * p.this_season.appearances + 1
    assert check_season_numbers(u).status == "fail"


def test_last_derby_scorers_must_match_the_score(built):
    universe, *_ = built
    u = copy(universe)
    lm = u.head_to_head[0].last_meeting
    lm.home_goals += 1
    assert check_head_to_head(u).status == "fail"


def test_top_scorer_table_must_match_the_numbers(built):
    universe, *_ = built
    u = copy(universe)
    u.top_scorers[0].rows[0].goals += 3
    assert check_top_scorers(u).status == "fail"


def test_concurrent_results_or_documents_fail(built):
    universe, match, docs, _ = built
    u = copy(universe)
    r = u.results[0].model_copy(update={"round": 9})
    u.results.append(r)
    assert check_no_concurrent_matches(u, match, docs).status == "fail"
    bad = {**docs, "league.md": docs["league.md"] + "\n- Matchday 9: Port Ellery FC 1-0 Ravensgate City\n"}
    assert check_no_concurrent_matches(universe, match, bad).status == "fail"


def test_sector_capacities_must_match_stadium_capacity(built):
    universe, match, *_ = built
    u = copy(universe)
    u.stadiums[0].stands[0].sectors[0].capacity += 100
    assert check_stadium(u, match).status == "fail"


# --- Content safety ------------------------------------------------------------------


def test_alcohol_word_fails(built):
    universe, _, _, blocklist = built
    u = copy(universe)
    u.stadiums[0].services[0].offer = "pies and a cold beer"
    assert check_content_safety(u, blocklist).status == "fail"


def test_multi_word_and_hyphenated_forbidden_phrases_fail(built):
    universe, _, _, blocklist = built
    for offer in ("ask about our free bet", "e-cigarette lounge"):
        u = copy(universe)
        u.stadiums[0].services[0].offer = offer
        bl = {**blocklist, "forbidden_words": blocklist["forbidden_words"] + ["free bet", "e-cigarette"]}
        assert check_content_safety(u, bl).status == "fail"


def test_allowed_prohibition_phrase_passes_but_word_elsewhere_fails(built):
    universe, _, _, blocklist = built
    bl = {**blocklist, "forbidden_words": blocklist["forbidden_words"] + ["smoke"], "allowed_phrases": ["smoke-free"]}
    u = copy(universe)
    u.stadiums[0].policies.append("The concourse is smoke-free.")
    assert check_content_safety(u, bl).status == "pass"
    u.stadiums[0].policies.append("Smoke area behind Gate C.")
    assert check_content_safety(u, bl).status == "fail"


def test_match_sponsor_has_a_games_prize_and_is_checked(built):
    universe, _, _, blocklist = built
    sponsor = universe.match_preview.sponsor
    assert sponsor.name and sponsor.sector and sponsor.prize.value_eur > 0
    assert "half-time games" in sponsor.prize.awarded_to
    assert check_content_safety(universe, blocklist).status == "pass"
    for sector in ("craft beer", "sports betting", "cigars"):  # check 6 reads the sponsor too
        u = copy(universe)
        u.match_preview.sponsor.sector = sector
        assert check_content_safety(u, blocklist).status == "fail", sector


# --- Games (UNIVERSO-SPEC 3) --------------------------------------------------------


@dataclass
class _Spell:
    club_id: str


@dataclass
class _P:
    id: str
    nationality: str
    clubs: list[str]
    club_id: str = "HOME"
    career: list = field(default_factory=list)

    def __post_init__(self):
        self.career = [_Spell(c) for c in self.clubs] + [_Spell(self.club_id)]


def test_grid_count_on_a_hand_made_squad():
    # Two players share six clubs and a nationality: every pair of the 7 criteria has 2 answers (a complete
    # graph K7). Grids = C(7,6) ways to pick 6 criteria x C(6,3)/2 ways to split them = 7 x 10 = 70; the 10 grids
    # made of the 6 clubs only have no nationality. A third player adds two clubs with a single answer: no change.
    squad = [_P("P1", "X", list("ABCDEF")), _P("P2", "X", list("ABCDEF")), _P("P3", "Y", ["G", "H"])]
    crit = games.criteria(squad, {"HOME"})
    assert ("club", "HOME") not in crit
    assert games.count_grids(crit) == 70
    assert games.count_grids(crit, with_nationality=True) == 60
    assert games.answers(crit, ("nationality", "X"), ("nationality", "Y")) == set()
    rows, cols = games.example_grid(crit)
    assert cols[-1] == ("nationality", "X") and len(rows) == 3


def test_games_check_fails_without_foreign_nationalities(built):
    universe, match, *_ = built
    u = copy(universe)
    for p in u.players:
        if p.nationality in ("Ireland", "France"):
            p.nationality = "Meridia"
    assert check_games(u, match).status == "fail"


def test_games_example_grid_has_two_answers_per_cell(built):
    universe, match, *_ = built
    squad = [p for p in universe.players if p.club_id in ("BRA", "CAL")]
    crit = {k: v for k, v in games.criteria(squad, {"BRA", "CAL"}).items() if k != ("nationality", "Meridia")}
    rows, cols = games.example_grid(crit)
    assert cols[-1][0] == "nationality"
    assert all(len(games.answers(crit, r, c)) >= 2 for r in rows for c in cols)


# --- Kit Man documents --------------------------------------------------------------


def test_docs_are_scoped_to_the_match(built):
    _, match, docs, _ = built
    assert {"matchday.md", "stadium.md", "rules.md", "league.md", "officials.md", "club-BRA.md", "club-CAL.md",
            "squad-BRA.md", "squad-CAL.md"} == set(docs)
    for text in docs.values():
        assert text.startswith("---\ndoc_id: ")
        assert f"match_id: {match.match_id}\n" in text


def test_substitute_in_the_starting_section_fails(built):
    universe, match, docs, _ = built
    text = docs["matchday.md"]
    sub = next(e for e in team(match, "BRA").lineup if not e.starting)
    p = next(x for x in universe.players if x.id == sub.player_id)
    line = f"- #{sub.shirt_number} {p.first_name} {p.last_name}, {POSITION_LABELS[sub.playing_position]}"
    moved = text.replace(line + "\n", "").replace("### Brackwater Athletic: starting XI (4-2-3-1)\n\n",
                                                   "### Brackwater Athletic: starting XI (4-2-3-1)\n\n" + line + "\n")
    assert moved != text
    assert check_documents(universe, match, {**docs, "matchday.md": moved}).status == "fail"


def test_every_player_has_a_section_with_the_key_facts(built):
    universe, match, docs, _ = built
    for p in universe.players:
        doc = docs[f"squad-{p.club_id}.md"]
        assert f"## #{p.shirt_number} {p.first_name} {p.last_name}\n" in doc
        assert f'Nickname: "{p.nickname}"' in doc and p.birthplace.town in doc


def test_kitman_docs_answer_the_spec_questions(built):
    universe, match, docs, _ = built
    club = docs["club-BRA.md"]
    chair = next(d.chairman for d in universe.club_details if d.club_id == "BRA")
    assert f"Chairman: {chair.first_name} {chair.last_name}" in club  # question 15
    assert "Penalties:" in docs["club-CAL.md"]  # question 4
    assert "per game" in docs["officials.md"]  # question 9
    assert "chance of rain" in docs["matchday.md"]  # question 11
    assert "Opened in 2004" in docs["stadium.md"]  # question 10
    assert "Job security" in docs["matchday.md"]  # question 8
    assert "top scorers" in docs["league.md"]  # question 5


def test_todays_set_piece_takers_skip_players_who_are_out(built):
    """Kit Man test (V-L3, question 4): today's takers come from the matchday squad, never a player who is out."""
    universe, match, docs, _ = built
    out = {p.id: p for p in universe.players if p.match_status == "not_selected"}
    today = docs["matchday.md"].split("## Penalties and set pieces today", 1)[1].split("\n## ", 1)[0]
    for d in universe.club_details:
        if d.club_id not in ("BRA", "CAL"):
            continue
        for ids in (d.set_pieces.penalties, d.set_pieces.free_kicks, d.set_pieces.corners):
            for pid in ids:
                p = universe.players[[x.id for x in universe.players].index(pid)]
                full = f"{p.first_name} {p.last_name}"
                if pid in out:
                    assert f"{full} (starting)" not in today and f"{full} (on the bench)" not in today
                    assert f"Not available today: {full}" in today or f", {full} (" in today
                    assert f"(#{p.shirt_number}, out today:" in docs[f"club-{d.club_id}.md"]
                else:
                    assert full in today
    assert "salary" not in "".join(docs.values()).lower()  # question 14: the data does not exist


def test_universe_serialises_to_json(built):
    universe, match, *_ = built
    data = json.loads(universe.model_dump_json())
    assert data["meta"]["seed"] == 2026
    assert len(data["clubs"]) == 44
    assert json.loads(match.model_dump_json())["match_id"] == "SYN-001"


def test_overview_names_the_match_every_club_and_a_grid(built):
    universe, match, docs, blocklist = built
    text = render_overview(universe, match, run_all(universe, match, blocklist, docs))
    assert text.startswith("# Synthetic data: overview")
    assert "Brackwater Athletic v Calderholm United" in text
    assert "## Tiki-Taka-Toe" in text
    for c in universe.clubs:
        assert f"| {c.name} |" in text
