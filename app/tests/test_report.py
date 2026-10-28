"""The final report in PDF (GET /api/recap/pdf) and the Recap's key numbers: the report shows what the app shows the
same ticket in the same version, and nothing else. A spy on the PDF builder keeps every text the report prints, with
its part (`_Report.printed`), so the tests read the report itself. No Foundry call (see conftest.py)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.contract import make_fake
from app.contract import mock as mock_module
from app.contract.mock import create_app as create_mock
from app.contract.models import Phase
from app.engine.config import EngineConfig
from app.engine.engine import Engine
from app.engine.server import create_app as create_engine
from app.engine.sources.fake import load_fake
from app.engine.sources.synthetic import load_synthetic
from app.report import pdf as report

from . import engine_fixture as fx

CODES = make_fake.FAKE_TICKET_CODES
SYN_DEMO, SYN_GOLD = "GNT-SYN001-D-WHQF-W1S6", "GNT-SYN001-G-DKXB-1W23"  # printed SYN-001 tickets
FAVOURITES = ["CAL-09", "BRA-09", "BRA-06"]
COMMON_PARTS = {"header", "summary", "key_moments", "match", "chart:shot_map", "running", "ball", "defence", "players",
                "footer"}
ANALYST_PARTS = {"chart:xg_cumulative", "chart:zone_map", "chart:speed_zones", "chart:defensive_shape",
                 "players_table"}
ANALYST_ROWS = ("PPDA", "xG, first half", "xG, whole match", "Average pass length", "Long balls",
                "Distance, ball in play", "Fastest 5 minutes of circulation", "Counter-attack minutes")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def built(monkeypatch) -> list:
    """The reports the server builds (the mock's and the engine's routes both go through `mock.recap_pdf`)."""
    reports = []

    def spy(*args):
        r = report.build_report(*args)
        reports.append(r)
        return bytes(r.output())

    monkeypatch.setattr(mock_module, "recap_pdf", spy)
    return reports


@pytest.fixture()
def mock_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("MOCK_PHASE", "full_time")  # silver and gold tickets follow the stadium clock
    return TestClient(create_mock())


@pytest.fixture(scope="module")
def syn(tmp_path_factory):
    """The tiny synthetic match with tracking (engine_fixture.py): every chart of the engine."""
    return load_synthetic("SYN-001", fx.build(tmp_path_factory.mktemp("syn")))


def fan(client: TestClient, code: str, mode: str, device: str = "device-0001") -> dict:
    """Logs in, confirms the 3 favourites in `mode`; demo tickets jump to full time."""
    r = client.post("/api/auth/login", json={"ticket_code": code, "device_id": device})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.put("/api/session/preferences", headers=h, json={
        "mode": mode, "favourite_player_ids": FAVOURITES, "favourites_locked": True, "language": "en"})
    assert r.status_code == 200, r.text
    if "-D-" in code or code in CODES["demo"]:
        assert client.post("/api/demo/phase", headers=h, json={"phase": "full_time"}).status_code == 200
    return h


def report_of(client: TestClient, h: dict, mode: str, built: list) -> tuple[report._Report, dict]:
    """The report the route builds, and the payloads the app gets for the same session and mode."""
    r = client.get(f"/api/recap/pdf?mode={mode}", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF"), r.text
    payloads = {"stats": client.get(f"/api/stats?mode={mode}", headers=h).json(),
                "recap": client.get(f"/api/recap?mode={mode}", headers=h).json(),
                "highlights": client.get("/api/highlights", headers=h).json()}
    return built[-1], payloads


def texts(rep: report._Report) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part, text in rep.printed:
        out.setdefault(part, []).append(text)
    return out


def payload_numbers(*payloads) -> dict[int, set[str]]:
    """Every number of the payloads (values, and numbers inside texts and field names such as km_15_20), printed with
    0 to 3 decimals."""
    values: set[float] = set()

    def walk(x) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            values.add(abs(float(x)))
        elif isinstance(x, str):
            values.update(float(t) for t in NUMBER.findall(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                values.update(float(t) for t in NUMBER.findall(k))
                walk(v)
        else:
            for v in x:
                walk(v)

    for p in payloads:
        walk(p)
    return {d: {f"{v:.{d}f}" for v in values} for d in range(4)}


def numbers_not_shown(rep: report._Report, payloads: dict) -> list[tuple[str, str, str]]:
    """The numbers of the report body (not the header with the date, nor the footer with the page) that no payload of
    the app has at the same precision."""
    shown = payload_numbers(payloads["stats"], payloads["highlights"], payloads["recap"])
    missing = []
    for part, text in rep.printed:
        if part in ("header", "footer"):
            continue
        for token in NUMBER.findall(text):
            digits = len(token.partition(".")[2])
            if f"{float(token):.{digits}f}" not in shown[min(digits, 3)]:
                missing.append((part, token, text))
    return missing


def assert_clean(rep: report._Report) -> None:
    """Nothing the app has dropped: no Archie (the summary is the code's), no acceleration load, no references, no
    real data provider."""
    everything = " ".join(t for _, t in rep.printed)
    for word in ("Archie", "World Cup", "Bundesliga", "DFL"):
        assert word not in everything
    assert "acceleration" not in everything.lower()
    assert "Summary computed from the match data." in texts(rep)["summary"]


def resolve(payload: dict, path: str):
    """'top_speed[3].kmh' -> payload['top_speed'][3]['kmh']."""
    v = payload
    for name, index in re.findall(r"(\w+)(?:\[(\d+)\])?", path):
        v = v[name]
        if index:
            v = v[int(index)]
    return v


def rep_moments(p: dict) -> list[dict]:
    """The key moments the report must show: the Recap's, in its order, from /api/highlights."""
    by_id = {x["highlight_id"]: x for x in p["highlights"]["highlights"]}
    return [by_id[i] for i in p["recap"]["highlight_ids"] if i in by_id]


# ------------------------------------------------------------------ the mock (every metric of the fake files)


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_each_version_has_the_sections_of_the_app(mock_client, built, mode) -> None:
    h = fan(mock_client, CODES["gold"][0], mode)
    rep, p = report_of(mock_client, h, mode, built)
    parts, by_part = {part for part, _ in rep.printed}, texts(rep)
    assert COMMON_PARTS | {"chart:momentum"} <= parts and "appendix" not in parts  # gold: no Inspector
    moments = rep_moments(p)  # the Recap's, in its order: title and table header first, then label and why
    assert moments and by_part["key_moments"][3::2] == [report._t(m["label"]) for m in moments]
    assert by_part["key_moments"][4::2] == [report._t(m["why_it_mattered"]) for m in moments]
    if mode == "casual":
        assert not parts & ANALYST_PARTS and rep.pages_count <= 2
        locked = by_part["locked"][0]
        titles = [c["title"] for c in p["stats"]["locked_charts"] if c["chart_id"] != "acceleration_load"]
        assert locked == "Available in the Analyst plan: " + ", ".join(titles + ["PPDA", "All players"]) + "."
        body = [t for part, t in rep.printed if part != "locked"]
        assert not [row for row in ANALYST_ROWS if row in body]
        assert "xG" not in by_part["chart:shot_map"] and "xG" not in by_part["players"]  # no xG column
    else:
        assert ANALYST_PARTS <= parts and "locked" not in parts and rep.pages_count <= 4
        assert {"PPDA", "xG, whole match", "Average pass length"} <= set(t for _, t in rep.printed)
        assert "xG" in by_part["chart:shot_map"] and "xG" in by_part["players"]
    assert_clean(rep)


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_every_number_of_the_report_is_in_the_app(mock_client, built, mode) -> None:
    h = fan(mock_client, CODES["demo"][0], mode)  # demo: the appendix with the Inspector's methods too
    rep, p = report_of(mock_client, h, mode, built)
    assert "appendix" in {part for part, _ in rep.printed}
    assert numbers_not_shown(rep, p) == []


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_the_appendix_is_the_inspectors(mock_client, built, mode) -> None:
    h = fan(mock_client, CODES["demo"][0], mode)
    rep, p = report_of(mock_client, h, mode, built)
    appendix = texts(rep)["appendix"]
    assert appendix[0] == "How these numbers were computed"
    methods = [m["metric"] for m in p["stats"]["inspector"]["methods"]]
    expected = [m for m in methods if m != "acceleration_load"
                and (mode == "analyst" or m not in report.ANALYST_METHODS)]
    assert len([t for t in appendix if t.endswith("% of data excluded")]) == len(expected)
    if mode == "casual":
        assert not {report.LABELS[m] for m in report.ANALYST_METHODS} & set(appendix)
    assert_clean(rep)


def test_the_report_follows_the_session(mock_client, built) -> None:
    """Silver: Fan only (403 for Analyst), the session's favourites in its order; 423 before the final whistle."""
    h = fan(mock_client, CODES["silver"][0], "casual")
    rep, p = report_of(mock_client, h, "casual", built)
    names = {x["player_id"]: x["name"] for t in mock_module.fake("match.json")["teams"] for x in t["lineup"]}
    wanted = [report._t(names[pid]) for pid in FAVOURITES]
    assert [t for t in texts(rep)["players"] if t in wanted] == wanted
    assert mock_client.get("/api/recap/pdf?mode=analyst", headers=h).status_code == 403
    h = fan(mock_client, CODES["demo"][1], "analyst", "device-0002")
    mock_client.post("/api/demo/phase", headers=h, json={"phase": "second_half"})
    assert mock_client.get("/api/recap/pdf", headers=h).status_code == 423


# ------------------------------------------------------------------ the engine


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_engine_report_without_frames_says_why(built, mode) -> None:
    """MATCH_SOURCE=fake (events only): the frame metrics are out, each with the reason of `unavailable`."""
    client = TestClient(create_engine(Engine(load_fake(), EngineConfig(speed=10.0), now=FakeTime())))
    h = fan(client, CODES["demo"][0], mode)
    rep, p = report_of(client, h, mode, built)
    notes = " ".join(t for _, t in rep.printed if " not available: " in t)
    assert "Distance covered" in notes and "needs tracking frames" in notes
    assert "chart:momentum" not in {part for part, _ in rep.printed}
    assert numbers_not_shown(rep, p) == []
    assert_clean(rep)


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_engine_report_with_frames(syn, built, mode) -> None:
    """The synthetic match with tracking: the engine's own numbers and VAR's moments, nothing the app does not show."""
    engine = Engine(syn, EngineConfig(speed=10.0), now=FakeTime())
    client = TestClient(create_engine(engine))
    engine.stadium.seek(engine.starts[Phase.full_time] + 1, False)
    h = fan(client, SYN_GOLD, mode)
    rep, p = report_of(client, h, mode, built)
    parts = {part for part, _ in rep.printed}
    assert COMMON_PARTS <= parts and not parts & (ANALYST_PARTS if mode == "casual" else {"locked"})
    assert numbers_not_shown(rep, p) == []
    assert_clean(rep)
    h = fan(client, SYN_DEMO, mode, "device-0002")
    rep, p = report_of(client, h, mode, built)
    assert "appendix" in {part for part, _ in rep.printed} and numbers_not_shown(rep, p) == []


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_recap_key_numbers_say_whose(syn, mode) -> None:
    """Each key number of the Recap points to the same value in Data & Statistics of the same mode; the top speed
    names its player, so the app can say whose it is."""
    engine = Engine(syn, EngineConfig(speed=10.0), now=FakeTime())
    client = TestClient(create_engine(engine))
    h = fan(client, SYN_DEMO, mode)
    recap = client.get(f"/api/recap?mode={mode}", headers=h).json()
    stats = client.get(f"/api/stats?mode={mode}", headers=h).json()
    assert {k["source_field"].split("[")[0].split(".")[0] for k in recap["key_numbers"]} == {"possession", "shots",
                                                                                              "top_speed"}
    for k in recap["key_numbers"]:
        assert resolve(stats, k["source_field"]) == k["value"], k
    top = next(k for k in recap["key_numbers"] if k["source_field"].startswith("top_speed"))
    entry = resolve(stats, top["source_field"].rsplit(".", 1)[0])
    assert top["arguments"]["player_id"] == entry["player_id"]
    assert top["value"] == max(x["kmh"] for x in stats["top_speed"])


def test_text_outside_latin1_is_replaced() -> None:
    assert report._t("Ó Fearghail – 54 % ≈ 1.2 km") == "Ó Fearghail - 54 % ~ 1.2 km"
    assert report._t("北") == "?"
