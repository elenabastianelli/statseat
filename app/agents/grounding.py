"""The grounding validator (AG-1b): every number in Archie's answer must come from a tool result or the MATCH CONTEXT.

`numbers_in` reads the numbers of a text in any of the app's languages (decimal point or comma, thousands
separators, "≈", a unit after the number). `check` matches each of them against the numbers of this question's
sources: the results of The Analyst's tools (by their stable paths, `numeric_fields`) and the MATCH CONTEXT; a shirt
number ("#4", "el número 4") only against the line-ups of the match, never against a statistic. A text
number matches a source number when it is that number at the precision written (4.13 km may be said as "4.1 km",
never as "4.2 km"). Matched numbers become the answer's `GroundedValue`s; the others are `numbers_unmatched` and the
answer does not go out as it is (`app/agents/archie.py`).

Limits, on purpose: numbers written in words ("two goals") are not read, and a small whole number can match an
unrelated field of a large result. The tools return only the numbers of the fan's version and instant, so a Fan
answer can never be grounded on an Analyst number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .analyst_tools import numeric_fields

# Numbers anyone may say without a source: the length of a match and of a half.
CONSTANTS = {90.0: "match length (minutes)", 45.0: "half length (minutes)"}

_UNIT = r"(?:km/h|km\b|m/s²|m/s|m\b|%|’|'|min\b|minutes?\b|mins?\b|s\b|seconds?\b|kph\b|mph\b)"
_NUM = re.compile(
    r"(?<![A-Za-z]-)(?<![A-Za-z\d.,])"  # not part of a code like E-4, VL4 or SYN-001
    r"(?P<approx>≈\s?|~\s?)?"
    r"(?P<num>\d{1,3}(?:[,.   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![\d])(?P<ord>st|nd|rd|th|º|°|ª|er\b)?"
    rf"(?:\s?(?P<unit>{_UNIT}))?"
)


@dataclass(frozen=True)
class Number:
    text: str  # as written, e.g. "≈ 4.1 km"
    value: float  # the most likely reading
    readings: tuple[tuple[float, int], ...]  # every reading (value, decimals): "60,400" is 60400 or 60.4
    unit: str | None
    ordinal: bool
    shirt: bool = False  # "#4", "number 4", "el número 4", "4号": a shirt number, checked against the line-ups


# What may come right before a shirt number, in the app's languages; Chinese writes 号 after it.
_SHIRT_BEFORE = re.compile(r"(?:#|\b(?:number|no\.|nº|n\.|n°|numero|número|numéro|nummer|nr\.|номер|№)\s?)$",
                           re.IGNORECASE)


def _readings(raw: str) -> tuple[tuple[float, int], ...]:
    out: list[tuple[float, int]] = []
    groups = re.split(r"[,.   ]", raw)
    seps = re.findall(r"[,.   ]", raw)
    if not seps:
        return ((float(raw), 0),)
    if len(seps) == 1 and seps[0] in ",.":  # 4.1, 1,2, 60,400 (thousands or a decimal)
        whole, frac = groups
        out.append((float(f"{whole}.{frac}"), len(frac)))
        if len(frac) == 3:
            out.append((float(whole + frac), 0))
        return tuple(out)
    # several separators: thousands groups, the last one may be the decimal part (60.400,5 or 60,400.5)
    if seps[-1] in ",." and len(set(seps[:-1])) == 1 and seps[-1] != seps[0]:
        out.append((float("".join(groups[:-1]) + "." + groups[-1]), len(groups[-1])))
    if all(len(g) == 3 for g in groups[1:]):
        out.append((float("".join(groups)), 0))
    return tuple(out) or ((float(re.sub(r"\D", "", raw)), 0),)


def numbers_in(text: str) -> list[Number]:
    out = []
    for m in _NUM.finditer(text):
        readings = _readings(m["num"])
        unit = m["unit"]
        if unit in ("’", "'"):
            unit = "'"
        before, after = text[max(0, m.start() - 12):m.start()], text[m.end():m.end() + 1]
        shirt = (bool(_SHIRT_BEFORE.search(before)) or after == "号") and len(readings) == 1 and readings[0][1] == 0
        out.append(Number(m.group(0).strip(), readings[0][0], readings, unit, bool(m["ord"]), shirt))
    return out


@dataclass(frozen=True)
class Source:
    """One number Archie may say: from a tool run (its arguments and path) or from the MATCH CONTEXT."""

    tool: str
    arguments: dict
    source_field: str
    value: float


def tool_sources(tool: str, arguments: dict, result: dict) -> list[Source]:
    if "error" in result:
        return []
    args = {k: v for k, v in arguments.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    return [Source(tool, args, path, float(v)) for path, v in numeric_fields(result).items()]


def context_sources(numbers: dict[str, list[float]]) -> list[Source]:
    return [Source("match_context", {}, label, v) for label, values in numbers.items() for v in values]


def lineup_sources(shirts: dict[str, int]) -> list[Source]:
    """The shirt numbers of the match (the engine's line-ups): the only source of a number written as a shirt."""
    return [Source("lineup", {"player": pid}, f"{pid}.shirt_number", float(n)) for pid, n in shirts.items()]


@dataclass
class Check:
    passed: bool
    grounding: list[dict] = field(default_factory=list)  # contract GroundedValue
    unmatched: list[str] = field(default_factory=list)
    checked: int = 0

    def contract(self) -> dict:
        """A contract `GroundingCheck` for the Inspector."""
        return {"passed": self.passed, "numbers_checked": self.checked, "numbers_unmatched": self.unmatched}


def _match(n: Number, sources: list[Source]) -> tuple[Source, float] | None:
    """The first source equal to a reading of `n`, else the first one that `n` writes at its precision."""
    for s in sources:
        for v, _ in n.readings:
            if abs(s.value - v) < 1e-9:
                return s, v
    for s in sources:
        for v, d in n.readings:
            if abs(round(s.value, d) - v) < 1e-9:
                return s, v
    return None


def check(text: str, sources: Iterable[Source]) -> Check:
    """Match every number of `text` against `sources` (in order of preference: tool results, then the context)."""
    sources = list(sources)
    shirts = [s for s in sources if s.tool == "lineup"]
    others = [s for s in sources if s.tool != "lineup"]
    out = Check(passed=True)
    for n in numbers_in(text):
        if n.ordinal and n.value in (1.0, 2.0):  # "1st half", "2º tempo": the halves of the match
            continue
        out.checked += 1
        hit = _match(n, shirts if n.shirt else others)
        if hit is None and n.value in CONSTANTS:
            hit = Source("laws_of_the_game", {}, CONSTANTS[n.value], n.value), n.value
        if hit is None:
            out.unmatched.append(n.text)
            continue
        src, value = hit
        out.grounding.append({"text": n.text, "value": value, "unit": n.unit, "tool": src.tool,
                              "arguments": src.arguments, "source_field": src.source_field})
    out.passed = not out.unmatched
    return out


def explain(unmatched: list[str]) -> str:
    return ", ".join(f'"{u}"' for u in unmatched)
