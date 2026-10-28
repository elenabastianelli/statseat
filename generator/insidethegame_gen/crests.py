"""Crests and kits of every club in the universe (task M-1 / S-11), drawn by code.

    cd generator
    .venv/bin/python -m insidethegame_gen.crests
    .venv/bin/python -m insidethegame_gen.crests --preview ../preview.html   # one page with everything

Outputs:
    synthetic/crests/<club_id>.svg          one crest per club (the tickets pick them up automatically)
    synthetic/kits/<club_id>_home.svg       shirt, shorts and socks
    synthetic/kits/<club_id>_away.svg
    synthetic/kits/colours.json             kit colours of every club, with their source

Brackwater (BRA) and Calderholm (CAL) are drawn by hand from their crest descriptions. The other clubs
share one template: a shield or a roundel (as the description says) in the club colours, a simple symbol
chosen from a keyword of the description, and the initials or the name on the ring. Kits of BRA and CAL
come from `club_details.kits`; for the others they are proposed from `colours` (home: primary shirt,
secondary shorts, primary socks; away: the reverse, or white/black when the two shirts would be too close).

Only plain SVG (paths, circles, rects, text with transforms, no textPath, clipPath or filters), because
svglib converts the crests for the PDF tickets. The same universe always gives the same files.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .crest_art import ART, SHAPES, SHIELDS

REPO_ROOT = Path(__file__).resolve().parents[2]
FONT = 'font-family="Helvetica, Arial, sans-serif" font-weight="bold"'


def crest_file(club_id: str) -> str:
    """Path of a club's crest, relative to the repository root (cited by match_info.json, task E-6)."""
    return f"synthetic/crests/{club_id}.svg"


def kit_file(club_id: str, side: str) -> str:
    """Path of a club's home or away kit, relative to the repository root (cited by match_info.json, task E-6)."""
    return f"synthetic/kits/{club_id}_{side}.svg"

# --- Colours ---------------------------------------------------------------------------------


def rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(rgb(a), rgb(b))) ** 0.5


def luminance(c: str) -> float:
    def lin(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable(colour: str, background: str) -> str:
    """The colour itself, or white/black when it does not stand out on the background."""
    if contrast(colour, background) >= 2.5:
        return colour
    return "#FFFFFF" if contrast("#FFFFFF", background) >= contrast("#111111", background) else "#111111"


def is_light(c: str) -> bool:
    r, g, b = rgb(c)
    return 0.299 * r + 0.587 * g + 0.114 * b > 150


def kit_colours(club: dict, details: dict | None) -> dict:
    if details and details.get("kits"):
        kits = details["kits"]
        return {side: {k: kits[side][k] for k in ("shirt", "shorts", "socks")} for side in ("home", "away")} | {
            "source": "universe club_details.kits"}
    p, s = club["colours"]["primary"], club["colours"]["secondary"]
    away = s
    if distance(p, s) < 120 or contrast(p, s) < 1.3:
        away = "#111111" if is_light(p) else "#FFFFFF"
    return {"home": {"shirt": p, "shorts": s, "socks": p},
            "away": {"shirt": away, "shorts": p, "socks": away},
            "source": "proposed from colours (M-1)"}


# --- Symbols (drawn in a 100 x 100 box) -------------------------------------------------------


def sym_bird(c: str, perched: bool) -> str:
    if perched:
        return (f'<path d="M22 58 Q30 34 56 38 Q66 26 78 32 L90 30 L80 38 Q80 56 62 64 L40 66 L18 80 Z" fill="{c}"/>'
                f'<path d="M50 66 L48 82 M60 64 L60 82" stroke="{c}" stroke-width="4" fill="none"/>'
                f'<rect x="30" y="82" width="44" height="5" fill="{c}"/>')
    return (f'<path d="M6 46 Q28 26 48 46 Q70 22 94 34 Q72 42 60 56 L52 72 L44 58 Q26 50 6 46 Z" fill="{c}"/>'
            f'<path d="M46 60 L36 76 L52 70 Z" fill="{c}"/>')


def sym_tools(c: str) -> str:
    one = (f'<rect x="46" y="18" width="8" height="70" rx="2" fill="{c}"/>'
           f'<path d="M30 12 Q50 2 70 12 L66 22 Q50 16 34 22 Z" fill="{c}"/>')
    return f'<g transform="rotate(-35 50 50)">{one}</g><g transform="rotate(35 50 50)">{one}</g>'


def sym_rails(c: str) -> str:
    one = (f'<rect x="38" y="4" width="6" height="92" fill="{c}"/><rect x="56" y="4" width="6" height="92" fill="{c}"/>'
           + "".join(f'<rect x="32" y="{y}" width="36" height="4" fill="{c}"/>' for y in (14, 30, 66, 82)))
    return f'<g transform="rotate(-40 50 50)">{one}</g><g transform="rotate(40 50 50)">{one}</g>'


def sym_horns(c: str) -> str:
    return (f'<path d="M36 40 Q50 34 64 40 L60 74 Q50 86 40 74 Z" fill="{c}"/>'
            f'<path d="M38 42 Q22 34 18 14 M24 30 Q14 28 10 20 M62 42 Q78 34 82 14 M76 30 Q86 28 90 20" '
            f'stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round"/>')


def sym_animal(c: str) -> str:
    return (f'<ellipse cx="46" cy="58" rx="30" ry="15" fill="{c}"/><circle cx="78" cy="48" r="11" fill="{c}"/>'
            f'<path d="M20 56 Q6 54 4 44" stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round"/>')


def sym_shuttle(c: str) -> str:
    return (f'<path d="M6 50 Q50 28 94 50 Q50 72 6 50 Z" fill="{c}"/>'
            f'<path d="M30 50 Q36 18 54 26 Q70 36 50 50 Q30 64 44 78 Q60 86 72 70" stroke="{c}" stroke-width="4" '
            f'fill="none"/>')


def sym_bell(c: str) -> str:
    big = f'<path d="M30 72 Q32 30 50 28 Q68 30 70 72 L76 78 L24 78 Z" fill="{c}"/><circle cx="50" cy="84" r="5" fill="{c}"/>'
    small = '<path d="M-8 12 Q-7 -6 0 -7 Q7 -6 8 12 L10 14 L-10 14 Z" fill="{c}"/>'.replace("{c}", c)
    return big + f'<g transform="translate(16 18)">{small}</g><g transform="translate(84 18)">{small}</g>'


def sym_windmill(c: str) -> str:
    return (f'<path d="M40 92 L44 46 L56 46 L60 92 Z" fill="{c}"/>'
            f'<path d="M50 44 L14 8 M50 44 L86 8 M50 44 L14 80 M50 44 L86 80" stroke="{c}" stroke-width="7" '
            f'stroke-linecap="round"/><circle cx="50" cy="44" r="6" fill="{c}"/>')


def sym_bridge(c: str) -> str:
    return (f'<path d="M4 40 H96 V72 H86 Q80 52 70 52 Q62 52 58 72 H42 Q38 52 30 52 Q20 52 14 72 H4 Z" fill="{c}"/>'
            f'<rect x="4" y="30" width="92" height="5" fill="{c}"/>')


def sym_cross(c: str) -> str:
    return (f'<rect x="44" y="8" width="12" height="62" fill="{c}"/><rect x="30" y="22" width="40" height="10" fill="{c}"/>'
            + "".join(f'<rect x="{x}" y="{y}" width="14" height="8" fill="{c}"/>'
                      for x, y in ((20, 74), (36, 74), (52, 74), (68, 74), (28, 84), (44, 84), (60, 84))))


def sym_flame(c: str) -> str:
    one = 'M0 0 Q-12 -20 0 -44 Q4 -28 12 -22 Q14 -12 0 0 Z'
    return (f'<path transform="translate(50 80)" d="{one}" fill="{c}"/>'
            f'<path transform="translate(30 82) scale(0.7)" d="{one}" fill="{c}"/>'
            f'<path transform="translate(70 82) scale(0.7)" d="{one}" fill="{c}"/>'
            f'<rect x="16" y="84" width="68" height="6" rx="3" fill="{c}"/>')


def sym_book(c: str) -> str:
    return (f'<path d="M8 40 Q30 32 48 42 V84 Q30 74 8 82 Z M92 40 Q70 32 52 42 V84 Q70 74 92 82 Z" fill="{c}"/>'
            f'<path d="M30 70 L84 10 Q88 16 82 22 L36 72 Z" fill="{c}"/>')


def sym_flag(c: str) -> str:
    return (f'<rect x="28" y="8" width="6" height="86" fill="{c}"/><path d="M34 12 L84 28 L34 44 Z" fill="{c}"/>'
            f'<rect x="16" y="90" width="30" height="5" fill="{c}"/>')


def sym_leaf(c: str) -> str:
    return (f'<path d="M50 6 Q86 36 52 86 Q14 40 50 6 Z" fill="{c}"/>'
            f'<path d="M50 20 L52 96" stroke="{c}" stroke-width="4"/>')


def sym_reeds(c: str) -> str:
    stems = "".join(f'<path d="M{x} 70 Q{x + d} 40 {x + d * 2} 14" stroke="{c}" stroke-width="4" fill="none"/>'
                    f'<ellipse cx="{x + d * 2}" cy="22" rx="4" ry="10" fill="{c}"/>'
                    for x, d in ((30, -3), (50, 0), (70, 3)))
    return stems + sym_wave(c, lines=(76, 88))


def sym_wave(c: str, lines: tuple[int, ...] = (40, 58, 76)) -> str:
    return "".join(f'<path d="M6 {y} Q17 {y - 10} 28 {y} T50 {y} T72 {y} T94 {y}" stroke="{c}" stroke-width="6" '
                   f'fill="none" stroke-linecap="round"/>' for y in lines)


def sym_boat(c: str) -> str:
    return (f'<path d="M8 56 H92 L80 72 H20 Z" fill="{c}"/><rect x="30" y="40" width="34" height="16" fill="{c}"/>'
            + sym_wave(c, lines=(84,)))


def sym_mountains(c: str) -> str:
    return f'<path d="M2 86 L30 34 L46 58 L64 18 L98 86 Z" fill="{c}"/>'


def sym_wheel(c: str) -> str:
    spokes = "".join(f'<path d="M50 50 L{50 + 34 * dx} {50 + 34 * dy}" stroke="{c}" stroke-width="5"/>'
                     for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1), (0.71, 0.71), (-0.71, 0.71), (0.71, -0.71),
                                    (-0.71, -0.71)))
    return f'<circle cx="50" cy="50" r="36" stroke="{c}" stroke-width="8" fill="none"/>{spokes}'


# First keyword found in the crest description wins.
SYMBOLS: list[tuple[tuple[str, ...], str]] = [
    (("heron", "raven", "lark ", "kingfisher", "gull", "grouse", "crow", "falcon", "moorhen", "wren", "grebe",
      "kestrel"), "bird"),
    (("stag", "bull"), "horns"),
    (("sheep", "otter"), "animal"),
    (("railway",), "rails"),
    (("shuttle",), "shuttle"),
    (("bell",), "bell"),
    (("windmill",), "windmill"),
    (("bridge",), "bridge"),
    (("market cross",), "cross"),
    (("flame", "furnace"), "flame"),
    (("quill", "book"), "book"),
    (("maypole", "pennant"), "flag"),
    (("pickaxe", "hammer", "chisel", "axe", "spade", "anvil", "crook"), "tools"),
    (("acorn", "fern", "holly"), "leaf"),
    (("reeds",), "reeds"),
    (("narrowboat", "boat"), "boat"),
    (("wave",), "wave"),
    (("peaks", "mountain"), "mountains"),
    (("wheel",), "wheel"),
]
PERCHED = ("perched", "standing", "swimming", "walking", " on a ")


def symbol(description: str, colour: str) -> tuple[str, str]:
    d = description.lower()
    for words, name in SYMBOLS:
        if any(w in d for w in words):
            if name == "bird":
                return name, sym_bird(colour, any(p in d for p in PERCHED))
            return name, globals()[f"sym_{name}"](colour)
    return "star", f'<path d="M50 8 L61 40 L95 40 L67 60 L78 92 L50 72 L22 92 L33 60 L5 40 L39 40 Z" fill="{colour}"/>'


# --- Crests ----------------------------------------------------------------------------------

SHIELD = "M24 24 H176 V126 Q176 204 100 234 Q24 204 24 126 Z"


def svg(view: str, body: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" role="img" aria-label="{title}">'
            f'<title>{title}</title>{body}</svg>\n')


def initials(name: str) -> str:
    words = [w for w in re.split(r"\s+", name) if w and w.upper() not in ("FC", "AFC")]
    return "".join(w[0] for w in words).upper()[:3]


def ring_text(text: str, radius: float, cx: float, cy: float, size: float, colour: str, bottom: bool) -> str:
    """Letters one by one along a circle (svglib has no textPath). Bottom text reads upright."""
    step = min(8.5, 250.0 / max(len(text), 1))
    start = -step * (len(text) - 1) / 2
    out = []
    for i, ch in enumerate(text):
        a = start + i * step
        if bottom:
            out.append(f'<text x="{cx}" y="{cy + radius + size * 0.35:.1f}" text-anchor="middle" font-size="{size}" '
                       f'{FONT} fill="{colour}" transform="rotate({-a:.2f} {cx} {cy})">{ch}</text>')
        else:
            out.append(f'<text x="{cx}" y="{cy - radius + size * 0.35:.1f}" text-anchor="middle" font-size="{size}" '
                       f'{FONT} fill="{colour}" transform="rotate({a:.2f} {cx} {cy})">{ch}</text>')
    return "".join(out)


def generic_crest(club: dict) -> tuple[str, str]:
    p, s = club["colours"]["primary"], club["colours"]["secondary"]
    desc = club["crest"]
    ink = readable(s, p)
    if club["id"] in ART:
        name, sym = "drawn", ART[club["id"]](ink, p)
    else:
        name, sym = symbol(desc, ink)
    if "roundel" in desc.lower():
        quoted = re.search(r"'([^']+)'", desc)
        text = (quoted.group(1) if quoted else f"{club['name']} {club['founded']}").upper()
        year = re.search(r"\s(\d{4})$", text)
        top, low = (text[:year.start()], year.group(1)) if year else (text, "")
        ring_ink = readable(p, s)
        body = (f'<circle cx="120" cy="120" r="116" fill="{s}"/><circle cx="120" cy="120" r="86" fill="{p}"/>'
                f'<circle cx="120" cy="120" r="115" stroke="{p}" stroke-width="2" fill="none"/>'
                + ring_text(top, 100, 120, 120, 17, ring_ink, bottom=False)
                + (ring_text(low, 100, 120, 120, 17, ring_ink, bottom=True) if low else "")
                + f'<g transform="translate(78 78) scale(0.84)">{sym}</g>')
        return name, svg("0 0 240 240", body, f"Crest of {club['name']}")
    extras = ""
    d = desc.lower()
    place = "translate(46 50) scale(1.08)"
    if "sash" in d:
        extras += f'<path d="M34 44 L42 36 L158 152 L150 160 Z" fill="{s}"/>'
        place = "translate(108 34) scale(0.55)"  # the small lake and boat in the upper corner
    if "chevron" in d:
        extras += f'<path d="M36 140 L100 72 L164 140 V162 L100 96 L36 162 Z" fill="{s}"/>'
        place = "translate(76 110) scale(0.48)"  # the holly leaf in the lower half
    if "blocks" in d:  # a wall of outlined blocks
        extras += "".join(f'<rect x="{x}" y="{y}" width="30" height="16" fill="none" stroke="{ink}" stroke-width="1.5"/>'
                          for k, y in enumerate(range(34, 164, 16))
                          for x in ((40, 70, 100, 130) if k % 2 else (25, 55, 85, 115, 145)) if 30 <= x and x + 30 <= 170)
    shield = SHIELDS[SHAPES.get(club["id"], "heater")]
    body = (f'<path d="{shield}" fill="{s}"/><path d="{shield}" transform="translate(100 129) scale(0.92) '
            f'translate(-100 -129)" fill="{p}"/>{extras}'
            f'<g transform="{place}">{sym}</g>'
            f'<text x="100" y="196" text-anchor="middle" font-size="22" {FONT} fill="{ink}">{initials(club["name"])}</text>')
    return name, svg("0 0 200 250", body, f"Crest of {club['name']}")


def crest_bra(club: dict) -> str:
    navy, amber = club["colours"]["primary"], club["colours"]["secondary"]
    chimneys = "".join(f'<rect x="{x}" y="6" width="14" height="22" fill="{amber}"/>' for x in (44, 76, 110, 142))
    links = "".join(f'<ellipse cx="{x}" cy="178" rx="9" ry="6" stroke="{amber}" stroke-width="4" fill="none"/>'
                    for x in range(52, 149, 16))
    lamp = (f'<rect x="96" y="96" width="8" height="66" fill="{amber}"/><rect x="82" y="160" width="36" height="8" '
            f'fill="{amber}"/><path d="M80 66 L120 66 L114 98 L86 98 Z" fill="{amber}"/>'
            f'<path d="M76 66 L100 48 L124 66 Z" fill="{amber}"/><rect x="92" y="72" width="16" height="20" '
            f'fill="{navy}"/><circle cx="100" cy="82" r="5" fill="{amber}"/>'
            f'<path d="M68 82 H58 M132 82 H142 M74 62 L66 54 M126 62 L134 54" stroke="{amber}" stroke-width="3"/>')
    ribbon = (f'<path d="M8 236 L30 226 L30 262 L8 272 L18 254 Z M192 236 L170 226 L170 262 L192 272 L182 254 Z" '
              f'fill="{amber}"/><path d="M28 226 Q100 244 172 226 V262 Q100 280 28 262 Z" fill="{amber}"/>'
              f'<text x="100" y="257" text-anchor="middle" font-size="17" {FONT} fill="{navy}">LUX IN PORTU</text>')
    body = (chimneys + f'<path d="{SHIELD}" transform="translate(0 4)" fill="{amber}"/>'
            f'<path d="{SHIELD}" transform="translate(100 133) scale(0.92) translate(-100 -129)" fill="{navy}"/>'
            + lamp + links + ribbon)
    return svg("0 0 200 280", body, f"Crest of {club['name']}")


def crest_cal(club: dict) -> str:
    maroon, sky = club["colours"]["primary"], club["colours"]["secondary"]
    kiln = ('<path d="M100 44 Q92 44 92 56 Q92 64 84 72 Q66 90 68 120 Q70 142 82 150 H118 Q130 142 132 120 '
            'Q134 90 116 72 Q108 64 108 56 Q108 44 100 44 Z" fill="#FFFFFF"/>'
            f'<path d="M92 150 V136 Q100 126 108 136 V150 Z" fill="{maroon}"/>')
    paddle = ('<rect x="-3" y="-34" width="6" height="44" fill="#FFFFFF"/>'
              '<ellipse cx="0" cy="16" rx="9" ry="14" fill="#FFFFFF"/>')
    paddles = (f'<g transform="translate(100 168) rotate(-55)">{paddle}</g>'
               f'<g transform="translate(100 168) rotate(55)">{paddle}</g>')
    body = (f'<circle cx="120" cy="120" r="116" fill="{sky}"/><circle cx="120" cy="120" r="86" fill="{maroon}"/>'
            f'<circle cx="120" cy="120" r="114" stroke="{maroon}" stroke-width="3" fill="none"/>'
            + ring_text("CALDERHOLM UNITED", 100, 120, 120, 17, maroon, bottom=False)
            + ring_text("1894", 100, 120, 120, 17, maroon, bottom=True)
            + f'<g transform="translate(20 -4)">{paddles}{kiln}</g>')
    return svg("0 0 240 240", body, f"Crest of {club['name']}")


# --- Kits ------------------------------------------------------------------------------------

SHIRT = "M40 14 L62 6 Q80 18 98 6 L120 14 L152 40 L136 64 L120 54 V128 H40 V54 L24 64 L8 40 Z"


def kit_svg(club: dict, side: str, colours: dict) -> str:
    k = colours[side]
    edge = "#111111"
    trim = colours["home"]["shorts"] if side == "home" else colours["away"]["shorts"]
    body = (f'<path d="{SHIRT}" fill="{k["shirt"]}" stroke="{edge}" stroke-width="1.5"/>'
            f'<path d="M62 6 Q80 26 98 6" stroke="{trim}" stroke-width="5" fill="none"/>'
            f'<path d="M40 136 H120 L124 186 H88 L80 160 L72 186 H36 Z" fill="{k["shorts"]}" stroke="{edge}" '
            f'stroke-width="1.5"/>'
            f'<rect x="44" y="194" width="22" height="40" rx="3" fill="{k["socks"]}" stroke="{edge}" stroke-width="1.5"/>'
            f'<rect x="94" y="194" width="22" height="40" rx="3" fill="{k["socks"]}" stroke="{edge}" stroke-width="1.5"/>')
    return svg("0 0 160 240", body, f"{side.capitalize()} kit of {club['name']}")


# --- Build -----------------------------------------------------------------------------------


def build(universe: dict) -> tuple[dict[str, str], dict]:
    """Returns {path relative to synthetic/: text} and a small report {club_id: symbol}."""
    details = {d["club_id"]: d for d in universe.get("club_details", [])}
    files: dict[str, str] = {}
    report: dict[str, str] = {}
    colours_all: dict[str, dict] = {}
    for club in universe["clubs"]:
        cid = club["id"]
        if cid == "BRA":
            files[f"crests/{cid}.svg"], report[cid] = crest_bra(club), "hand-drawn"
        elif cid == "CAL":
            files[f"crests/{cid}.svg"], report[cid] = crest_cal(club), "hand-drawn"
        else:
            report[cid], files[f"crests/{cid}.svg"] = generic_crest(club)
        colours = kit_colours(club, details.get(cid))
        colours_all[cid] = colours
        for side in ("home", "away"):
            files[f"kits/{cid}_{side}.svg"] = kit_svg(club, side, colours)
    files["kits/colours.json"] = json.dumps(colours_all, indent=2, ensure_ascii=False) + "\n"
    return files, report


def preview_html(universe: dict, files: dict[str, str]) -> str:
    cells = []
    for club in universe["clubs"]:
        cid = club["id"]
        cells.append(f'<figure><div class="c">{files[f"crests/{cid}.svg"]}</div>'
                     f'<div class="k">{files[f"kits/{cid}_home.svg"]}{files[f"kits/{cid}_away.svg"]}</div>'
                     f'<figcaption><b>{cid}</b> {club["name"]}<br><small>{club["crest"]}</small></figcaption></figure>')
    return ("<!doctype html><meta charset='utf-8'><title>Crests and kits</title><style>"
            "body{font:13px system-ui;background:#f4f4f2;margin:16px}main{display:grid;"
            "grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}figure{background:#fff;margin:0;"
            "padding:10px;border-radius:8px}.c svg{height:150px;width:100%}.k{display:flex;justify-content:center}"
            ".k svg{height:80px}small{color:#666}</style><main>" + "".join(cells) + "</main>")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", type=Path, help="also write one HTML page with every crest and kit")
    args = parser.parse_args()
    universe = json.loads((REPO_ROOT / "synthetic" / "universe" / "universe.json").read_text(encoding="utf-8"))
    files, report = build(universe)
    out = REPO_ROOT / "synthetic"
    for rel, text in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    if args.preview:
        args.preview.write_text(preview_html(universe, files), encoding="utf-8")
    symbols: dict[str, list[str]] = {}
    for cid, name in report.items():
        symbols.setdefault(name, []).append(cid)
    print(f"{sum(r.startswith('crests/') for r in files)} crests, {sum(r.endswith('.svg') and r.startswith('kits/') for r in files)} kits")
    for name, ids in sorted(symbols.items()):
        print(f"  {name:<11} {' '.join(ids)}")


if __name__ == "__main__":
    main()
