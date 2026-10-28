"""Player profile texts from the hand-written building blocks in `content/profiles.json`.

Nicknames, playing styles, strengths and curiosities are picked with the seeded
generator, and every {placeholder} is filled from the player's own data (height,
hometown, first club, loan club...), so a text never contradicts the universe.
Each nickname and each curiosity is used at most once per squad.
"""

from __future__ import annotations

import random
import re

from .careers import season_start


def _ok(when: dict | None, ctx: dict) -> bool:
    if not when:
        return True
    if when.get("never"):
        return False
    checks = {
        "groups": lambda v: ctx["group"] in v,
        "min_height": lambda v: ctx["height"] >= v,
        "max_height": lambda v: ctx["height"] <= v,
        "min_age": lambda v: ctx["age"] >= v,
        "max_age": lambda v: ctx["age"] <= v,
        "foot": lambda v: ctx["foot"] == v,
        "outfield": lambda v: (ctx["group"] != "GK") == v,
        "meridian": lambda v: ctx["meridian"] == v,
        "foreign": lambda v: (not ctx["meridian"]) == v,
        "academy": lambda v: ctx["academy"] == v,
        "has_loan": lambda v: bool(ctx.get("loan_club")) == v,
        "first_spell_goals": lambda v: (ctx["first_spell_goals"] > 0) == v,
        "min_career_goals": lambda v: ctx["career_goals"] >= v,
        "small_town": lambda v: ctx["small_town"] == v,
    }
    return all(checks[k](v) for k, v in when.items())


def fill(template: str, ctx: dict) -> str:
    return template.format(**ctx)


def surname_short(last: str) -> str | None:
    """'Thornfield' -> 'Thorny'; only for one-word surnames that start with a clear syllable."""
    if " " in last or "-" in last or len(last) < 6:
        return None
    m = re.match(r"^([^aeiouy]*[aeiouy]+[^aeiouy]+)", last.lower())
    if not m or not 3 <= len(m.group(1)) <= 5:
        return None
    stem = last[: len(m.group(1))]
    return stem + ("y" if not stem.endswith("y") else "o")


def pick_nickname(rng: random.Random, ctx: dict, content: dict, taken: set[str]) -> tuple[str, str]:
    options = []
    for n in content["nicknames"]:
        if _ok(n.get("when"), ctx):
            options.append((fill(n["nickname"], ctx), fill(n["origin"], ctx)))
    short = surname_short(ctx["last"])
    if short:
        options += [(short, fill(o, ctx)) for o in content["surname_nickname_origins"]]
    options = [o for o in options if o[0].lower() not in taken]
    if not options:
        initials = f"{ctx['first'][0]}{ctx['last'].replace('van ', '').replace('de ', '')[0]}".upper()
        options = [(initials, fill(content["initials_nickname_origin"], ctx))]
    nick, origin = rng.choice(options)
    taken.add(nick.lower())
    return nick, origin


def playing_style(rng: random.Random, ctx: dict, content: dict) -> tuple[str, list[str]]:
    styles = content["styles"][ctx["group"]]
    text = styles[ctx["style"][0]]
    if len(ctx["style"]) > 1:
        text += " " + content["second_style"].format(style=ctx["style"][1])
    strengths = sorted(rng.sample(content["strengths"][ctx["group"]], k=3))
    return text, strengths


def pick_curiosities(rng: random.Random, ctx: dict, content: dict, taken: set[str], computed: list[str]) -> list[str]:
    want = 2 if rng.random() < 0.3 else 1
    out = list(computed[:2])
    pool = [c for c in content["curiosities"] if _ok(c.get("when"), ctx) and c["text"] not in taken]
    rng.shuffle(pool)
    while len(out) < max(want, 1) and pool:
        c = pool.pop()
        taken.add(c["text"])
        out.append(fill(c["text"], ctx))
    return out[:2]


def international(rng: random.Random, nationality: str, tier: str, age: int, group: str, quality: float) -> tuple[int, int]:
    """Senior caps and goals for the player's national team."""
    if tier == "prospect" or age < 20 or rng.random() > 0.25 + quality * 0.6:
        return 0, 0
    caps = int(round(max(0, age - 20) * rng.uniform(0.5, 4.5) * quality))
    rate = {"ST": 0.3, "AM": 0.18, "W": 0.15, "CM": 0.06, "DM": 0.03, "FB": 0.02, "CB": 0.04, "GK": 0.0}[group]
    goals = sum(rng.random() < rate for _ in range(caps))
    return caps, goals


def first_season(career) -> int:
    return season_start(career[0].from_season)
