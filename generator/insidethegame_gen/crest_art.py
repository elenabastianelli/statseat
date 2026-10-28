"""One drawing per club, following the crest description in the universe (M-1).

Every function returns plain SVG for a 100 x 100 box: `c` is the ink (the club's second colour, made
readable), `bg` the field colour, used to cut holes and details. Brackwater and Calderholm are in
crests.py. SHAPES gives each shield club its own outline, so the crests do not all look alike.
"""

from __future__ import annotations

from math import cos, radians, sin


def star(cx: float, cy: float, r: float, c: str) -> str:
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.42
        a = -90 + i * 36
        pts.append(f"{cx + rad * cos(radians(a)):.1f},{cy + rad * sin(radians(a)):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{c}"/>'


def waves(c: str, ys: tuple[float, ...], width: float = 5, x0: float = 6, x1: float = 94) -> str:
    out = []
    for y in ys:
        d, x, up = f"M{x0} {y}", x0, True
        while x < x1:
            d += f" Q{x + 5.5} {y - 7 if up else y + 7} {x + 11} {y}"
            x, up = x + 11, not up
        out.append(f'<path d="{d}" stroke="{c}" stroke-width="{width}" fill="none" stroke-linecap="round"/>')
    return "".join(out)


def reeds(c: str, xs: tuple[float, ...], base: float, top: float) -> str:
    return "".join(f'<path d="M{x} {base} Q{x + (x - 50) * 0.08} {(base + top) / 2} {x + (x - 50) * 0.15} {top}" '
                   f'stroke="{c}" stroke-width="3" fill="none"/>'
                   f'<ellipse cx="{x + (x - 50) * 0.15:.1f}" cy="{top + 6}" rx="3" ry="7" fill="{c}"/>' for x in xs)


# --- Birds -----------------------------------------------------------------------------------


def fen(c: str, bg: str) -> str:  # white heron on one leg among marsh reeds
    return (f'<ellipse cx="50" cy="50" rx="17" ry="9" transform="rotate(-18 50 50)" fill="{c}"/>'
            f'<path d="M36 54 L22 64 L38 58 Z" fill="{c}"/>'
            f'<path d="M62 44 Q74 34 64 26 Q56 18 68 12" stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round"/>'
            f'<circle cx="69" cy="12" r="5" fill="{c}"/><path d="M72 10 L92 14 L72 15 Z" fill="{c}"/>'
            f'<path d="M50 58 L50 90 M50 72 L58 66" stroke="{c}" stroke-width="3" fill="none"/>'
            + reeds(c, (14, 22, 80, 88), 92, 52))


def rav(c: str, bg: str) -> str:  # silver raven, wings spread, on a city gate, under a small crown
    gate = (f'<path d="M24 66 H76 V96 H24 Z" fill="{c}"/>'
            + "".join(f'<rect x="{x}" y="60" width="8" height="7" fill="{c}"/>' for x in (24, 38, 54, 68))
            + f'<path d="M40 96 V82 A10 10 0 0 1 60 82 V96 Z" fill="{bg}"/>')
    raven = (f'<path d="M50 40 L8 18 L16 32 L4 34 L18 42 L10 48 L50 52 L90 48 L82 42 L96 34 L84 32 L92 18 Z" fill="{c}"/>'
             f'<circle cx="50" cy="34" r="7" fill="{c}"/><path d="M54 34 L62 37 L54 38 Z" fill="{c}"/>'
             f'<path d="M44 52 L50 62 L56 52 Z" fill="{c}"/>')
    crown = f'<path d="M40 22 L42 10 L46 16 L50 6 L54 16 L58 10 L60 22 Z" fill="{c}"/>'
    return gate + raven + crown


def wes(c: str, bg: str) -> str:  # navy lark in flight over an oval running track
    return (f'<ellipse cx="50" cy="70" rx="44" ry="20" stroke="{c}" stroke-width="5" fill="none"/>'
            f'<ellipse cx="50" cy="70" rx="32" ry="12" stroke="{c}" stroke-width="2" fill="none"/>'
            f'<path d="M20 30 Q34 16 46 30 Q60 12 80 18 Q64 26 56 38 L50 48 L46 38 Q34 34 20 30 Z" fill="{c}"/>'
            f'<path d="M80 18 L88 16 L82 22 Z" fill="{c}"/>')


def kin(c: str, bg: str) -> str:  # orange kingfisher diving into a wavy river
    return (f'<path d="M26 6 Q40 10 48 20 Q58 30 60 46 L56 62 L50 48 Q36 40 30 24 Z" fill="{c}"/>'
            f'<path d="M44 22 L18 30 L40 34 Z" fill="{c}"/><path d="M58 52 L64 76 L54 58 Z" fill="{c}"/>'
            f'<circle cx="54" cy="44" r="2.5" fill="{bg}"/>'
            + waves(c, (80, 92), 4))


def wex(c: str, bg: str) -> str:  # white gull gliding over a lighthouse
    return (f'<path d="M46 94 L50 40 L62 40 L66 94 Z" fill="{c}"/>'
            f'<rect x="50" y="56" width="12" height="6" fill="{bg}"/><rect x="49" y="74" width="14" height="6" fill="{bg}"/>'
            f'<rect x="48" y="30" width="16" height="10" fill="{c}"/><path d="M46 30 L56 20 L66 30 Z" fill="{c}"/>'
            f'<path d="M68 34 L94 26 M68 36 L94 44" stroke="{c}" stroke-width="2"/>'
            f'<path d="M6 30 Q16 18 26 28 Q36 18 46 26" stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round"/>')


def ske(c: str, bg: str) -> str:  # moorland grouse with heather sprigs at its feet
    heather = "".join(f'<path d="M{x} 96 L{x + dx} 74" stroke="{c}" stroke-width="2"/>'
                      + "".join(f'<circle cx="{x + dx * k / 4:.1f}" cy="{96 - 22 * k / 4:.1f}" r="2.6" fill="{c}"/>'
                                for k in (2, 3, 4)) for x, dx in ((12, -4), (22, 4), (78, -4), (88, 4)))
    return (f'<path d="M22 68 Q22 44 44 40 Q50 24 62 24 Q72 26 72 34 L82 38 L72 40 Q76 60 60 70 Z" fill="{c}"/>'
            f'<path d="M24 66 L10 58 L16 72 Z" fill="{c}"/><path d="M60 30 Q66 26 70 30" stroke="{bg}" stroke-width="3" fill="none"/>'
            f'<path d="M44 70 L42 84 M54 70 L56 84" stroke="{c}" stroke-width="3"/>' + heather)


def cro(c: str, bg: str) -> str:  # yellow crow on top of a steep yellow hill
    return (f'<path d="M4 98 Q36 92 50 58 L58 44 L66 58 Q78 90 96 98 Z" fill="{c}"/>'
            f'<path d="M44 38 Q46 22 60 22 L70 18 L66 26 Q72 36 62 40 Z" fill="{c}"/>'
            f'<path d="M46 36 L30 44 L44 42 Z" fill="{c}"/><path d="M56 40 L54 46 M62 40 L62 46" stroke="{c}" stroke-width="2"/>')


def fal(c: str, bg: str) -> str:  # light blue falcon diving, wings folded, over a mountain ridge
    return (f'<path d="M40 4 Q56 14 54 40 L46 66 L40 72 L36 60 Q28 30 40 4 Z" fill="{c}"/>'
            f'<path d="M42 14 L54 44 M38 18 L36 50" stroke="{bg}" stroke-width="2"/>'
            f'<path d="M2 98 L22 72 L34 84 L58 56 L76 74 L84 66 L98 98 Z" fill="{c}"/>')


def brm(c: str, bg: str) -> str:  # white moorhen walking on a white reed bed
    return (f'<path d="M24 48 Q30 28 52 30 Q60 18 70 20 Q78 24 74 32 L84 34 L74 38 Q72 56 56 60 L34 60 Z" fill="{c}"/>'
            f'<path d="M26 46 L14 36 L20 52 Z" fill="{c}"/>'
            f'<path d="M44 60 L38 80 L30 82 M58 60 L62 80 L72 82" stroke="{c}" stroke-width="3" fill="none"/>'
            f'<rect x="6" y="84" width="88" height="6" fill="{c}"/>'
            + "".join(f'<path d="M{x} 84 L{x + 3} 70" stroke="{c}" stroke-width="2"/>' for x in range(10, 94, 8)))


def lar(c: str, bg: str) -> str:  # lilac lark on a sprig of larkspur
    florets = "".join(f'<ellipse cx="{70 + (6 if k % 2 else -6)}" cy="{30 + k * 9}" rx="6" ry="4" fill="{c}"/>'
                      for k in range(6))
    return (f'<path d="M70 98 L70 22" stroke="{c}" stroke-width="3"/>' + florets
            + f'<path d="M14 54 Q16 36 34 36 Q40 26 50 28 Q56 30 54 36 L62 38 L54 42 Q54 54 40 58 Z" fill="{c}"/>'
            f'<path d="M16 52 L4 46 L10 60 Z" fill="{c}"/><path d="M40 58 L44 66 L66 66" stroke="{c}" stroke-width="2.5" fill="none"/>')


def wre(c: str, bg: str) -> str:  # cream wren on a cream mill wheel
    paddles = "".join(f'<rect x="47" y="40" width="6" height="12" fill="{c}" transform="rotate({a} 50 70)"/>'
                      for a in range(0, 360, 30))
    return (f'<circle cx="50" cy="70" r="24" stroke="{c}" stroke-width="5" fill="none"/>'
            f'<circle cx="50" cy="70" r="5" fill="{c}"/>'
            + "".join(f'<path d="M50 70 L{50 + 22 * dx:.1f} {70 + 22 * dy:.1f}" stroke="{c}" stroke-width="3"/>'
                      for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)))
            + paddles
            + f'<path d="M36 34 Q36 18 52 18 Q60 12 66 18 L74 18 L66 24 Q64 36 50 38 Z" fill="{c}"/>'
            f'<path d="M38 30 L28 8 L42 26 Z" fill="{c}"/>')


def gry(c: str, bg: str) -> str:  # white grebe swimming on wavy white lines
    return (f'<path d="M14 66 Q20 50 50 52 Q60 52 62 46 L60 26 Q62 16 70 18 L80 22 L72 24 L70 46 Q72 66 50 70 Z" '
            f'fill="{c}"/><path d="M66 18 L62 8 L70 16 M72 18 L74 8 L74 18" stroke="{c}" stroke-width="2.5" fill="none"/>'
            + waves(c, (80, 92), 4))


def kes(c: str, bg: str) -> str:  # peach kestrel hovering over a valley, fog across the base
    return (f'<path d="M50 22 L6 14 L18 24 L4 30 L44 34 L42 52 L50 60 L58 52 L56 34 L96 30 L82 24 L94 14 Z" fill="{c}"/>'
            f'<circle cx="50" cy="22" r="7" fill="{c}"/>'
            f'<path d="M4 64 L40 92 M96 64 L60 92" stroke="{c}" stroke-width="4"/>'
            + waves(c, (82, 94), 2.5, 10, 90))


# --- Animals ---------------------------------------------------------------------------------


def mar(c: str, bg: str) -> str:  # black drover's crook and a bull's head above a narrow valley
    return (f'<path d="M34 24 Q50 18 66 24 L64 44 Q62 58 50 62 Q38 58 36 44 Z" fill="{c}"/>'
            f'<path d="M36 26 Q20 26 14 12 Q26 18 36 20 M64 26 Q80 26 86 12 Q74 18 64 20" stroke="{c}" stroke-width="5" '
            f'fill="none" stroke-linecap="round"/><circle cx="44" cy="38" r="3" fill="{bg}"/><circle cx="56" cy="38" r="3" fill="{bg}"/>'
            f'<path d="M84 96 L84 40 Q84 28 94 28 Q100 30 98 38" stroke="{c}" stroke-width="5" fill="none"/>'
            f'<path d="M8 72 L44 96 M76 72 L56 96" stroke="{c}" stroke-width="4"/>')


def gle(c: str, bg: str) -> str:  # gold stag's head with antlers before three mountain peaks
    return (f'<path d="M2 96 L22 62 L36 78 L50 52 L66 78 L80 60 L98 96" stroke="{c}" stroke-width="3" fill="none"/>'
            f'<path d="M40 40 Q50 34 60 40 L58 70 Q50 82 42 70 Z" fill="{c}"/>'
            f'<path d="M42 42 Q26 34 22 10 M30 30 Q18 28 12 18 M26 20 L18 8 M58 42 Q74 34 78 10 M70 30 Q82 28 88 18 M74 20 L82 8" '
            f'stroke="{c}" stroke-width="4" fill="none" stroke-linecap="round"/>'
            f'<path d="M38 44 L30 40 L36 50 Z M62 44 L70 40 L64 50 Z" fill="{c}"/>')


def dun(c: str, bg: str) -> str:  # green shepherd's crook and a green sheep below, on a hillside base
    wool = "".join(f'<circle cx="{x}" cy="{y}" r="9" fill="{c}"/>' for x, y in ((36, 62), (48, 58), (60, 62), (42, 70), (56, 70)))
    return (f'<path d="M50 50 L50 10 Q50 2 60 2 Q68 4 66 12" stroke="{c}" stroke-width="5" fill="none"/>' + wool
            + f'<ellipse cx="70" cy="62" rx="7" ry="6" fill="{c}"/><path d="M40 76 L40 84 M56 76 L56 84" stroke="{c}" stroke-width="3"/>'
            f'<path d="M0 98 Q30 80 60 86 Q82 88 100 80 V100 H0 Z" fill="{c}"/>')


def hlm(c: str, bg: str) -> str:  # light blue otter swimming across a lake
    return (f'<path d="M8 56 Q20 44 44 46 Q62 46 72 40 Q80 34 88 38 Q94 42 88 48 Q80 52 72 54 Q56 62 30 62 Q16 62 8 56 Z" '
            f'fill="{c}"/><circle cx="84" cy="42" r="2" fill="{bg}"/><path d="M8 56 Q0 54 2 46" stroke="{c}" stroke-width="4" fill="none"/>'
            + waves(c, (70, 82, 94), 3.5))


# --- Objects and places ----------------------------------------------------------------------


def pel(c: str, bg: str) -> str:  # teal sea wall and three breaking waves
    wall = "".join(f'<rect x="{x}" y="{y}" width="18" height="9" fill="{c}"/>'
                   for y, xs in ((76, (4, 24, 44, 64, 84)), (87, (14, 34, 54, 74))) for x in xs if x + 18 <= 100)
    curl = 'M0 0 Q2 -18 18 -20 Q30 -20 32 -10 Q26 -16 20 -12 Q14 -6 22 0 Z'
    return wall + "".join(f'<path transform="translate({x} {y})" d="{curl}" fill="{c}"/>'
                          for x, y in ((6, 70), (36, 62), (66, 70)))


def ost(c: str, bg: str) -> str:  # white weaving shuttle with a thread looping into a knot
    return (f'<path d="M4 50 Q50 30 96 50 Q50 70 4 50 Z" fill="{c}"/><rect x="38" y="46" width="24" height="8" rx="3" fill="{bg}"/>'
            f'<path d="M50 50 Q30 20 50 16 Q70 14 62 34 Q54 50 40 70 Q34 86 52 86 Q66 84 58 72 Q52 64 44 76" '
            f'stroke="{c}" stroke-width="3" fill="none"/>')


def sal(c: str, bg: str) -> str:  # two crossed white pickaxes over a pile of salt crystals
    pick = (f'<rect x="47" y="14" width="6" height="62" fill="{c}"/>'
            f'<path d="M24 22 Q50 4 76 22 L72 26 Q50 14 28 26 Z" fill="{c}"/>')
    crystals = "".join(f'<path d="M{x} {y} l6 -8 l6 8 l-6 8 Z" fill="{c}"/>'
                       for x, y in ((22, 86), (34, 86), (46, 86), (58, 86), (70, 86), (28, 76), (40, 76), (52, 76), (64, 76), (46, 66)))
    return (f'<g transform="rotate(-38 50 46)">{pick}</g><g transform="rotate(38 50 46)">{pick}</g>' + crystals)


def ell(c: str, bg: str) -> str:  # cream cathedral bell, two smaller bells above it on either side
    big = (f'<path d="M28 84 Q30 40 50 36 Q70 40 72 84 L80 90 L20 90 Z" fill="{c}"/>'
           f'<rect x="46" y="28" width="8" height="10" fill="{c}"/><circle cx="50" cy="94" r="5" fill="{c}"/>'
           f'<path d="M32 76 H68" stroke="{bg}" stroke-width="2"/>')
    small = f'<path d="M-9 14 Q-8 -4 0 -6 Q8 -4 9 14 L12 17 L-12 17 Z" fill="{c}"/><circle cx="0" cy="20" r="3" fill="{c}"/>'
    return big + f'<g transform="translate(14 14)">{small}</g><g transform="translate(86 14)">{small}</g>'


def ald(c: str, bg: str) -> str:  # two gold railway lines crossing in an X, an alder leaf above the junction
    rail = (f'<rect x="40" y="26" width="5" height="76" fill="{c}"/><rect x="55" y="26" width="5" height="76" fill="{c}"/>'
            + "".join(f'<rect x="34" y="{y}" width="32" height="4" fill="{c}"/>' for y in (34, 48, 82, 94)))
    leaf = (f'<path d="M50 4 Q66 14 58 30 Q50 38 42 30 Q34 14 50 4 Z" fill="{c}"/>'
            f'<path d="M50 8 L50 40" stroke="{bg}" stroke-width="1.5"/>')
    return f'<g transform="rotate(-35 50 64)">{rail}</g><g transform="rotate(35 50 64)">{rail}</g>' + leaf


def hol(c: str, bg: str) -> str:  # black holly leaf (the chevron is drawn by the shield)
    return (f'<path d="M50 6 L58 20 L72 18 L66 32 L80 40 L66 48 L72 62 L58 60 L50 76 L42 60 L28 62 L34 48 L20 40 '
            f'L34 32 L28 18 L42 20 Z" fill="{c}"/><path d="M50 12 L50 90" stroke="{c}" stroke-width="3"/>'
            f'<circle cx="58" cy="84" r="5" fill="{c}"/><circle cx="66" cy="80" r="5" fill="{c}"/>')


def cop(c: str, bg: str) -> str:  # black anvil with a crossed hammer and tongs
    hammer = f'<rect x="47" y="2" width="6" height="56" fill="{c}"/><rect x="38" y="0" width="24" height="12" rx="2" fill="{c}"/>'
    tongs = f'<path d="M48 58 L46 6 L40 0 M52 58 L54 6 L60 0" stroke="{c}" stroke-width="4" fill="none"/>'
    anvil = f'<path d="M14 58 H80 Q92 58 96 50 Q90 70 70 70 L66 82 L80 92 H24 L38 82 L34 70 Q20 68 14 58 Z" fill="{c}"/>'
    return f'<g transform="rotate(-35 50 40)">{hammer}</g><g transform="rotate(35 50 40)">{tongs}</g>' + anvil


def low(c: str, bg: str) -> str:  # white reeds rising from three wavy white lines of water
    return reeds(c, (28, 40, 50, 60, 72), 70, 12) + waves(c, (72, 82, 92), 4)


def tar(c: str, bg: str) -> str:  # small white lake with a boat (top corner; the sash is drawn by the shield)
    return (f'<ellipse cx="50" cy="60" rx="46" ry="24" fill="#FFFFFF"/>'
            f'<path d="M24 56 H76 L68 70 H32 Z" fill="{bg}"/><path d="M50 56 V20 L74 50 H50" fill="{bg}"/>')


def pfo(c: str, bg: str) -> str:  # gold woodsman's axe across a gold pine tree
    tree = (f'<path d="M50 2 L70 30 H60 L76 54 H62 L80 80 H20 L38 54 H24 L40 30 H30 Z" fill="{c}"/>'
            f'<rect x="45" y="80" width="10" height="16" fill="{c}"/>')
    axe = (f'<rect x="12" y="50" width="80" height="6" rx="2" fill="{bg}" stroke="{c}" stroke-width="2.5" '
           f'transform="rotate(-30 50 53)"/><path d="M74 26 Q92 28 96 44 L80 46 Z" fill="{c}" stroke="{bg}" stroke-width="2"/>')
    return tree + axe


def qua(c: str, bg: str) -> str:  # blue quarry hammer at the centre (the wall of blocks is drawn by the shield)
    return (f'<rect x="20" y="22" width="60" height="22" rx="3" fill="{c}"/><rect x="45" y="44" width="10" height="52" fill="{c}"/>'
            f'<path d="M20 22 L10 33 L20 44 Z M80 22 L90 33 L80 44 Z" fill="{c}"/>')


def bre(c: str, bg: str) -> str:  # white windmill, its four sails forming an X
    sail = f'<path d="M0 0 L-6 -40 L6 -40 Z" fill="{c}"/>'
    return (f'<path d="M38 96 L42 46 L58 46 L62 96 Z" fill="{c}"/><path d="M38 46 L50 34 L62 46 Z" fill="{c}"/>'
            f'<rect x="46" y="80" width="8" height="16" fill="{bg}"/>'
            + "".join(f'<g transform="translate(50 40) rotate({a})">{sail}</g>' for a in (45, 135, 225, 315))
            + f'<circle cx="50" cy="40" r="5" fill="{c}"/>')


def van(c: str, bg: str) -> str:  # white iron bridge of three arches across the middle
    arches = "".join(f'<path d="M{x} 74 Q{x + 14} 46 {x + 28} 74" fill="{bg}"/>' for x in (8, 36, 64))
    truss = "".join(f'<path d="M{x} 30 L{x + 10} 44 L{x + 20} 30" stroke="{c}" stroke-width="2.5" fill="none"/>'
                    for x in range(4, 92, 20))
    return (f'<rect x="2" y="44" width="96" height="30" fill="{c}"/>' + arches
            + f'<rect x="2" y="26" width="96" height="5" fill="{c}"/>' + truss + waves(c, (88,), 3))


def hew(c: str, bg: str) -> str:  # village green, a maypole, five stars for the five divisions climbed
    stars = "".join(star(x, y, 6, c) for x, y in ((14, 22), (30, 12), (50, 8), (70, 12), (86, 22)))
    return (stars + f'<rect x="47" y="24" width="6" height="64" fill="{c}"/><circle cx="50" cy="24" r="5" fill="{c}"/>'
            f'<path d="M50 30 Q36 50 24 76 M50 30 Q64 50 76 76 M50 30 Q44 54 38 80 M50 30 Q56 54 62 80" '
            f'stroke="{c}" stroke-width="2" fill="none"/><path d="M0 98 Q50 78 100 98 Z" fill="{c}"/>')


def oak(c: str, bg: str) -> str:  # gold acorn hanging from a gold oak leaf
    leaf = (f'<path d="M50 4 Q60 8 58 16 Q70 14 68 24 Q78 26 72 36 Q80 42 68 46 L54 48 L50 56 L46 48 L32 46 '
            f'Q20 42 28 36 Q22 26 32 24 Q30 14 42 16 Q40 8 50 4 Z" fill="{c}"/>'
            f'<path d="M50 10 L50 52" stroke="{bg}" stroke-width="2"/>')
    acorn = (f'<path d="M50 56 L50 62" stroke="{c}" stroke-width="3"/><path d="M34 70 Q50 56 66 70 Z" fill="{c}"/>'
             f'<path d="M36 72 H64 Q64 94 50 98 Q36 94 36 72 Z" fill="{c}"/>'
             f'<path d="M36 72 H64" stroke="{bg}" stroke-width="2"/>')
    return leaf + acorn


def mir(c: str, bg: str) -> str:  # light grey canal narrowboat on wavy lines, under a grey lock gate
    gate = (f'<rect x="14" y="4" width="6" height="40" fill="{c}"/><rect x="80" y="4" width="6" height="40" fill="{c}"/>'
            f'<rect x="14" y="10" width="72" height="5" fill="{c}"/><path d="M20 15 L50 40 L80 15" stroke="{c}" stroke-width="3" fill="none"/>'
            f'<path d="M50 15 V40" stroke="{c}" stroke-width="3"/>')
    boat = (f'<path d="M6 64 H94 L88 76 H12 Z" fill="{c}"/><rect x="18" y="54" width="62" height="10" fill="{c}"/>'
            + "".join(f'<rect x="{x}" y="57" width="6" height="4" fill="{bg}"/>' for x in (24, 36, 48, 60, 72)))
    return gate + boat + waves(c, (88,), 3.5)


def emb(c: str, bg: str) -> str:  # three burnt-orange flames rising from a bed of glowing embers
    flame = 'M0 0 Q-14 -20 -2 -46 Q2 -30 10 -26 Q16 -14 0 0 Z'
    embers = "".join(f'<circle cx="{x}" cy="{y}" r="6" fill="{c}"/>'
                     for x, y in ((16, 88), (30, 90), (44, 88), (58, 90), (72, 88), (86, 90), (24, 80), (52, 80), (78, 80)))
    return (f'<path transform="translate(50 76)" d="{flame}" fill="{c}"/>'
            f'<path transform="translate(26 78) scale(0.75)" d="{flame}" fill="{c}"/>'
            f'<path transform="translate(74 78) scale(0.75)" d="{flame}" fill="{c}"/>' + embers)


def stw(c: str, bg: str) -> str:  # red stonemason's chisel and mallet crossed over a granite block
    chisel = f'<rect x="47" y="4" width="6" height="58" fill="{c}"/><path d="M45 62 H55 L50 72 Z" fill="{c}"/>'
    mallet = f'<rect x="47" y="14" width="6" height="56" fill="{c}"/><rect x="34" y="2" width="32" height="18" rx="4" fill="{c}"/>'
    block = (f'<rect x="16" y="70" width="68" height="26" fill="{c}"/>'
             f'<path d="M30 76 L38 84 M58 74 L66 80 M44 88 L52 90" stroke="{bg}" stroke-width="2"/>')
    return f'<g transform="rotate(-35 50 44)">{chisel}</g><g transform="rotate(35 50 44)">{mallet}</g>' + block


def qui(c: str, bg: str) -> str:  # yellow quill pen crossing an open yellow book
    return (f'<path d="M6 46 Q28 36 48 48 V92 Q28 80 6 90 Z M94 46 Q72 36 52 48 V92 Q72 80 94 90 Z" fill="{c}"/>'
            f'<path d="M14 56 Q28 52 40 58 M14 66 Q28 62 40 68 M60 58 Q72 52 86 56 M60 68 Q72 62 86 66" '
            f'stroke="{bg}" stroke-width="2" fill="none"/>'
            f'<path d="M22 84 L80 6 Q94 4 90 18 L30 86 Z" fill="{c}" stroke="{bg}" stroke-width="2"/>')


def tid(c: str, bg: str) -> str:  # a single white breaking wave filling the lower half
    return (f'<path d="M0 98 V64 Q10 40 40 36 Q70 34 78 54 Q70 46 60 50 Q50 56 60 64 Q76 72 96 62 V98 Z" fill="{c}"/>'
            f'<path d="M78 54 Q88 46 96 54" stroke="{c}" stroke-width="3" fill="none"/>')


def irm(c: str, bg: str) -> str:  # orange furnace pouring a stream of molten iron into a ladle
    return (f'<path d="M14 96 V40 L22 24 H46 L54 40 V96 Z" fill="{c}"/><rect x="24" y="8" width="16" height="16" fill="{c}"/>'
            f'<rect x="26" y="58" width="16" height="14" fill="{bg}"/><path d="M54 52 H70 V58 H54 Z" fill="{c}"/>'
            f'<path d="M70 56 Q76 60 74 74" stroke="{c}" stroke-width="5" fill="none"/>'
            f'<path d="M58 76 H92 Q90 96 75 96 Q60 96 58 76 Z" fill="{c}"/><rect x="88" y="70" width="10" height="5" fill="{c}"/>')


def cbf(c: str, bg: str) -> str:  # white market cross (a stone pillar with a ball, no cross bar) on white cobblestones
    return (f'<circle cx="50" cy="22" r="8" fill="{c}"/><rect x="40" y="30" width="20" height="5" fill="{c}"/>'
            f'<rect x="45" y="35" width="10" height="27" fill="{c}"/>'
            f'<rect x="38" y="62" width="24" height="5" fill="{c}"/><rect x="32" y="67" width="36" height="5" fill="{c}"/>'
            + "".join(f'<rect x="{x}" y="{y}" width="14" height="8" rx="3" fill="{c}"/>'
                      for y, xs in ((75, (8, 24, 40, 56, 72)), (86, (16, 32, 48, 64, 80))) for x in xs if x + 14 <= 96))


def pnb(c: str, bg: str) -> str:  # black pennant on a black pole, the pennant carrying a small gold star
    return (f'<rect x="20" y="6" width="6" height="90" fill="{c}"/><circle cx="23" cy="6" r="5" fill="{c}"/>'
            f'<path d="M26 12 L94 30 L26 50 Z" fill="{c}"/>' + star(46, 31, 8, bg))


def frh(c: str, bg: str) -> str:  # pale green fern frond curling from bottom to top
    stem = 'M30 98 Q40 60 60 40 Q80 20 66 10 Q56 6 58 18'
    leaflets = "".join(f'<ellipse cx="{x}" cy="{y}" rx="9" ry="3.5" transform="rotate({a} {x} {y})" fill="{c}"/>'
                       for x, y, a in ((28, 84, -20), (44, 84, 40), (32, 70, -30), (50, 68, 30), (40, 56, -40),
                                       (58, 54, 20), (50, 44, -50), (68, 44, 10), (62, 32, -60), (78, 30, 0)))
    return f'<path d="{stem}" stroke="{c}" stroke-width="4" fill="none"/>' + leaflets


def mrb(c: str, bg: str) -> str:  # white spade and watering can crossed beneath a marrow
    marrow = (f'<path d="M14 22 Q14 6 50 8 Q86 10 86 24 Q84 36 50 34 Q16 34 14 22 Z" fill="{c}"/>'
              f'<path d="M86 20 L96 16" stroke="{c}" stroke-width="3"/><path d="M24 20 Q50 14 76 22" stroke="{bg}" stroke-width="2" fill="none"/>')
    spade = (f'<rect x="47" y="40" width="6" height="40" fill="{c}"/><path d="M40 80 H60 V92 Q50 100 40 92 Z" fill="{c}"/>'
             f'<rect x="42" y="36" width="16" height="6" fill="{c}"/>')
    can = (f'<rect x="38" y="60" width="26" height="24" rx="3" fill="{c}"/><path d="M64 66 L82 50" stroke="{c}" stroke-width="4"/>'
           f'<path d="M44 60 Q51 48 58 60" stroke="{c}" stroke-width="3" fill="none"/>')
    return marrow + f'<g transform="rotate(-30 50 66)">{spade}</g><g transform="rotate(25 50 72)">{can}</g>'


ART = {"FEN": fen, "PEL": pel, "RAV": rav, "OST": ost, "MAR": mar, "SAL": sal, "GLE": gle, "WES": wes, "KIN": kin,
       "ELL": ell, "ALD": ald, "HOL": hol, "COP": cop, "WEX": wex, "LOW": low, "TAR": tar, "PFO": pfo, "QUA": qua,
       "BRE": bre, "DUN": dun, "SKE": ske, "VAN": van, "HEW": hew, "OAK": oak, "MIR": mir, "CRO": cro, "EMB": emb,
       "FAL": fal, "STW": stw, "BRM": brm, "QUI": qui, "TID": tid, "LAR": lar, "WRE": wre, "GRY": gry, "IRM": irm,
       "CBF": cbf, "HLM": hlm, "PNB": pnb, "KES": kes, "FRH": frh, "MRB": mrb}

# Shield outlines in a 200 x 250 box (field from x 24 to 176).
SHIELDS = {
    "heater": "M24 24 H176 V126 Q176 204 100 234 Q24 204 24 126 Z",
    "round": "M24 24 H176 V150 A76 76 0 0 1 24 150 Z",
    "french": "M24 24 H176 V176 Q176 214 138 220 Q110 222 100 234 Q90 222 62 220 Q24 214 24 176 Z",
    "peaked": "M24 42 L100 18 L176 42 V130 Q176 200 100 234 Q24 200 24 130 Z",
    "notched": "M24 24 Q62 42 100 24 Q138 42 176 24 V130 Q176 204 100 234 Q24 204 24 130 Z",
    "square": "M24 24 H176 V196 Q176 210 162 214 L100 234 L38 214 Q24 210 24 196 Z",
}
SHAPES = {"FEN": "heater", "RAV": "peaked", "OST": "round", "MAR": "notched", "GLE": "peaked", "WES": "round",
          "ELL": "french", "ALD": "square", "HOL": "heater", "COP": "square", "LOW": "round", "TAR": "heater",
          "PFO": "notched", "QUA": "square", "DUN": "round", "SKE": "french", "VAN": "square", "OAK": "notched",
          "MIR": "french", "CRO": "peaked", "EMB": "heater", "FAL": "peaked", "STW": "square", "QUI": "french",
          "LAR": "round", "WRE": "notched", "GRY": "heater", "IRM": "square", "CBF": "french", "PNB": "peaked",
          "KES": "notched", "FRH": "round", "MRB": "french"}
