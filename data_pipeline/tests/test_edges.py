"""Tracking-area edges (R-EDGE) and parking values (R-PARKING) are discovered, never hard-coded (guidelines v4, section 5.2)."""

import numpy as np

from insidethegame_data.config import DEFAULT_CONFIG as CFG
from insidethegame_data.edges import discover_edges, discover_parking_values, edge_mask, parking_mask
from insidethegame_data.prepare import CODE, prepare_half

from .arrays import FS, make_half, run_x


def _pitch_100x60_with_edges():
    rng = np.random.default_rng(1)
    x = list(rng.uniform(-50, 50, 2000)) + [52.0] * 30 + [52.0] * 20 + [51.2] * 3
    y = list(rng.uniform(-30, 30, 2000)) + list(np.linspace(-5, 5, 30)) + [1.0] * 20 + [0.0] * 3
    # An edge beyond a touchline (2.5 m) on the other axis, and a frozen point beyond the touchline that is not an edge.
    x += list(np.linspace(-20, 0, 40)) + [10.0] * 60
    y += [-32.5] * 40 + [31.0] * 60
    x += [12.0]
    y += [-32.0]  # a real point between the pitch and the edge
    return np.array(x), np.array(y)


def test_r_edge_discovered_on_another_pitch_and_distance():
    x, y = _pitch_100x60_with_edges()
    out = discover_edges(x, y, 50.0, 30.0, CFG.edge_min_samples)
    found = {(e["axis"], e["value_m"]) for e in out["edges"]}
    assert found == {("x", 52.0), ("y", -32.5)}
    rej = {(c["axis"], c["value_m"]) for c in out["candidates_rejected"]}
    assert ("y", 31.0) in rej  # one frozen position: not an edge (and y = 31.0 is not even the outermost... it is, but single position)


def test_r_edge_no_edge_when_nothing_is_pinned():
    rng = np.random.default_rng(2)
    out = discover_edges(rng.uniform(-55, 55, 5000), rng.uniform(-36, 36, 5000), 52.5, 34.0, CFG.edge_min_samples)
    assert out["edges"] == []


def test_r_edge_samples_are_outside_tracking_area_with_window():
    n = 500
    x = run_x(n, 2.0, 30.0)  # runs towards the goal line at x = 50
    x[200:260] = 52.0  # pinned 2 m behind the line for 2.4 s, other coordinate moving
    x[260:] = 49.0 - run_x(240, 2.0)
    y = np.linspace(0, 3, n)
    edges = discover_edges(x, y, 50.0, 30.0, CFG.edge_min_samples)["edges"]
    assert edges and edges[0]["value_m"] == 52.0
    hr = prepare_half(make_half([x], [y]), [], edges, (100.0, 60.0), CFG)
    w = int(FS)
    assert (hr.reason[200 - w:260 + w, 0] == CODE["outside_tracking_area"]).all()
    assert edge_mask(x, y, edges).sum() == 60


def test_r_edge_single_frozen_player_beyond_a_line_is_not_an_edge_in_a_2d_array():
    """Regression (J03WOY): one player frozen 2.7 m beyond the touchline for 645 frames, in a (frames x players)
    array with other players moving. Runs must be counted along each track, so this is one run from one position:
    rejected. Two players pinned at the same value from different positions is an edge."""
    n = 700
    rng = np.random.default_rng(3)
    x = rng.uniform(-50, 50, (n, 3))
    y = rng.uniform(-30, 30, (n, 3))
    x[20:665, 0], y[20:665, 0] = 10.0, 32.7  # frozen beyond the touchline: not an edge
    out = discover_edges(x, y, 50.0, 30.0, CFG.edge_min_samples)
    assert out["edges"] == []
    c = [c for c in out["candidates_rejected"] if c["value_m"] == 32.7][0]
    assert c["runs"] == 1 and c["tracks"] == 1 and c["distinct_positions"] == 1
    x[100:140, 1], y[100:140, 1] = 52.0, np.linspace(0, 4, 40)  # pinned on the goal-line edge, other coordinate moving
    x[300:330, 2], y[300:330, 2] = 52.0, 7.0  # another player pinned there, other coordinate frozen
    out = discover_edges([x[:350], x[350:]], [y[:350], y[350:]], 50.0, 30.0, CFG.edge_min_samples)
    e = [e for e in out["edges"] if e["axis"] == "x"][0]
    assert e["value_m"] == 52.0 and e["runs"] == 2 and e["tracks"] == 2 and e["samples"] == 70


# ---------------------------------------------------------------------------
# R-PARKING
# ---------------------------------------------------------------------------

def _parking_fixture(n=600, players_at_value=3):
    """Pitch 100 x 60 (touchline at y = 30). P0, P1 and P3 (v4.2: ``parking_min_tracks`` = 3) are frozen at y = +32.5
    (2.5 m beyond the touchline) for 1.2 s each, at different x (``players_at_value`` = 2: only P0 and P1); P2 is frozen
    alone further out, at y = +33.0, for 2 s (the outermost value: not an edge, one position, one run). Everyone else
    moves on the pitch."""
    t = np.arange(n) / FS
    xs, ys = [], []
    for c in range(4):
        xs.append(-30 + 10 * c + 2 * np.sin(t / 3 + c))
        ys.append(10 * np.cos(t / 4 + c))
    f = np.arange(n)
    for c, (lo, x0) in list(zip((0, 1, 3), ((100, -10.0), (300, 15.0), (200, 30.0))))[:players_at_value]:
        a, b = lo - 75, lo + 29 + 75  # walks out to the value at 1 m/s or less, stays 1.2 s, walks back
        kx, ky = [0, a, lo, lo + 29, b, n - 1], [None] * 6
        kx_vals = [xs[c][0], xs[c][a], x0, x0, xs[c][b], xs[c][-1]]
        ky_vals = [ys[c][0], ys[c][a], 32.5, 32.5, ys[c][b], ys[c][-1]]
        seg = (f >= a) & (f <= b)
        xs[c][seg] = np.interp(f[seg], kx, kx_vals)
        ys[c][seg] = np.interp(f[seg], kx, ky_vals)
    a, b = 400 - 75, 449 + 75
    seg = (f >= a) & (f <= b)
    xs[2][seg] = np.interp(f[seg], [a, 400, 449, b], [xs[2][a], 5.0, 5.0, xs[2][b]])
    ys[2][seg] = np.interp(f[seg], [a, 400, 449, b], [ys[2][a], 33.0, 33.0, ys[2][b]])
    return xs, ys


def test_r_parking_shared_value_is_discovered_and_a_single_frozen_player_is_not():
    xs, ys = _parking_fixture()
    x, y = np.column_stack(xs), np.column_stack(ys)
    edges = discover_edges(x, y, 50.0, 30.0, CFG.edge_min_samples)["edges"]
    assert edges == []  # y = 33.0 is one frozen position (one run), y = 32.5 is not the outermost value
    out = discover_parking_values([x], [y], [["P0", "P1", "P2", "P3"]], 50.0, 30.0, int(FS), CFG.parking_min_samples, CFG.parking_min_tracks, edges)
    assert [(v["axis"], v["value_m"], v["players"], v["runs"], v["samples"]) for v in out["parking_values"]] == [("y", 32.5, 3, 3, 90)]
    rej = {c["value_m"]: c["reason"] for c in out["candidates_rejected"]}
    assert "fewer than 3 players" in rej[33.0]


def test_r_parking_v4_1_two_players_are_not_enough():
    """R-PARKING (v4.1): two players frozen side by side at the same value beyond a line (for example a goal celebration
    near the corner flag, J03WOY) are not a provider parking value: at least 3 players are needed."""
    xs, ys = _parking_fixture(players_at_value=2)
    x, y = np.column_stack(xs), np.column_stack(ys)
    out = discover_parking_values([x], [y], [["P0", "P1", "P2", "P3"]], 50.0, 30.0, int(FS), CFG.parking_min_samples, CFG.parking_min_tracks)
    assert CFG.parking_min_tracks == 3 and out["parking_values"] == []
    rej = {c["value_m"]: c["reason"] for c in out["candidates_rejected"]}
    assert "fewer than 3 players" in rej[32.5]


def test_r_parking_samples_including_the_first_are_parked_with_1_s_on_each_side():
    xs, ys = _parking_fixture()
    values = discover_parking_values([np.column_stack(xs)], [np.column_stack(ys)], [["P0", "P1", "P2", "P3"]], 50.0, 30.0, int(FS),
                                     CFG.parking_min_samples, CFG.parking_min_tracks)["parking_values"]
    hr = prepare_half(make_half(xs, ys), [], [], (100.0, 60.0), CFG, parking=values)
    w = int(FS)
    assert (hr.reason[100 - w:130 + w, 0] == CODE["parked"]).all()  # the run (first sample included) + 1 s each side
    assert hr.valid[100 - w - 1, 0]
    g = [g for g in hr.gaps if g["kind"] == "parked" and g["col"] == 0][0]
    assert g["lo"] == 100 and g["hi"] == 129
    assert abs(g["resume_m"] - float(np.hypot(xs[0][130] - xs[0][99], ys[0][130] - ys[0][99]))) < 1e-9  # from the sample before the run
    # The lone frozen player beyond the line is only frozen: his first sample stays valid.
    assert hr.valid[400, 2] and (hr.reason[401:450, 2] == CODE["frozen"]).all()
    assert parking_mask(np.array([1.0, 1.0, 2.0]), np.array([32.5, 32.5, 32.5]), values).tolist() == [True, True, False]


def test_r_parking_an_edge_value_is_never_a_parking_value():
    xs, ys = _parking_fixture()
    ys[2][ys[2] > 32.0] = 32.0  # P2 now frozen inside the parking value: 32.5 is the outermost
    x, y = np.column_stack(xs), np.column_stack(ys)
    edges = discover_edges(x, y, 50.0, 30.0, CFG.edge_min_samples)["edges"]
    assert [(e["axis"], e["value_m"]) for e in edges] == [("y", 32.5)]  # outermost, 2 positions, 2 runs: an edge
    out = discover_parking_values([x], [y], [["P0", "P1", "P2", "P3"]], 50.0, 30.0, int(FS), CFG.parking_min_samples, CFG.parking_min_tracks, edges)
    assert out["parking_values"] == []


def test_r_parking_inherited_frozen_position_counts_as_one_track():
    """R-SUB-INHERIT: a substitute whose track starts at the outgoing player's frozen last position continues the same
    track: the outgoing tail and the inherited head are one track, not two players at a parking value."""
    xs, ys = _parking_fixture()
    x, y = np.column_stack(xs), np.column_stack(ys)
    ids = [["P0", "P1", "P2", "P3"]]
    args = (50.0, 30.0, int(FS), CFG.parking_min_samples, CFG.parking_min_tracks)
    assert len(discover_parking_values([x], [y], ids, *args)["parking_values"]) == 1
    assert discover_parking_values([x], [y], ids, *args, same_track={"P1": "P0"})["parking_values"] == []
