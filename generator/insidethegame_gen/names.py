"""Invented names for every generated person, with the surname rule of the universe.

Rules (UNIVERSO-SPEC section 1.1, guidelines section 12):
- no surname is ever given to two people of the universe (hand-written people included);
- a first name is not repeated among the people of the match and never copies an official's;
- anything on the blocklist (famous full names and surnames, real club tokens) is skipped.

Meridian surnames come from the hand-written pool plus prefix + suffix combinations, so
there are enough of them for the coaching history.
"""

from __future__ import annotations

import random
import re
from collections import Counter


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if t]


def contains_word(text: str, word: str) -> bool:
    """Case-insensitive whole-word (or whole-phrase) match; hyphens and spaces are boundaries."""
    t, w = tokens(text), tokens(word)
    return bool(w) and any(t[i:i + len(w)] == w for i in range(len(t) - len(w) + 1))


class WordSet:
    """Fast whole-word lookup for many words or phrases (same semantics as `contains_word`)."""

    def __init__(self, words: list[str]):
        self.phrases = {tuple(tokens(w)) for w in words if tokens(w)}
        self.lengths = sorted({len(p) for p in self.phrases})

    def found_in(self, text: str) -> bool:
        t = tokens(text)
        return any(tuple(t[i:i + n]) in self.phrases for n in self.lengths for i in range(len(t) - n + 1))


def is_blocked_surname(last: str, famous_surnames: WordSet) -> bool:
    return famous_surnames.found_in(last)


def generated_meridian_surnames(parts: dict, avoid_words: set[str]) -> list[str]:
    """Prefix + suffix combinations in a fixed order (the order matters for determinism)."""
    out = []
    for p in parts["prefixes"]:
        for s in parts["suffixes"]:
            name = p + s
            # skip doubled letters at the join ("Wood" + "dale") and words that are already place or club names
            if len(name) < 5 or name.lower() in avoid_words or p[-1].lower() == s[0].lower():
                continue
            out.append(name)
    return out


class NamePicker:
    """Draws invented names; keeps track of every surname and first name in use."""

    def __init__(self, rng: random.Random, names: dict, blocklist: dict, reserved: list[tuple[str, str]],
                 avoid_words: set[str]):
        self.rng = rng
        self.first_pool: dict[str, list[str]] = names["first_names"]
        famous = WordSet(blocklist["famous_surnames"])
        self.famous_full = {n.lower() for n in blocklist["famous_full_names"]}
        club_tokens = WordSet(blocklist["real_club_tokens"])
        avoid = {w.lower() for w in avoid_words}

        def allowed(last: str) -> bool:
            return not famous.found_in(last) and last.lower() not in avoid and not club_tokens.found_in(last)

        pools = {nat: [s for s in lst if allowed(s)] for nat, lst in names["surnames"].items()}
        extra = [s for s in generated_meridian_surnames(names["meridia_surname_parts"], avoid) if allowed(s)]
        self.hand_written = {s for lst in pools.values() for s in lst}
        known = {s.lower() for s in pools["Meridia"]}
        pools["Meridia"] += [s for s in extra if s.lower() not in known]
        self.surname_pool = pools
        self.used_surnames = {last.lower() for _, last in reserved}
        self.first_count: Counter[str] = Counter(first.lower() for first, _ in reserved)
        self.protected_first: set[str] = set()  # first names that nobody else may use (officials, match people)
        self.match_stems: set[str] = {last.lower()[:3] for _, last in reserved}  # look-alike surnames in the match

    def protect(self, first: str) -> None:
        self.protected_first.add(first.lower())

    def capacity(self, nationality: str) -> int:
        return sum(1 for s in self.surname_pool.get(nationality, []) if s.lower() not in self.used_surnames)

    def pick(self, nationality: str, unique_first: bool) -> tuple[str, str]:
        """A new (first, last) pair.

        With `unique_first` (people of the match) the first name must be unused in the universe and the surname
        must not start like another match surname (same first three letters, e.g. Lockton and Lockholme)."""
        surnames = [s for s in self.surname_pool[nationality] if s.lower() not in self.used_surnames]
        if unique_first:  # people of the match: no two surnames that start with the same three letters
            surnames = [s for s in surnames if s.lower()[:3] not in self.match_stems]
            hand = [s for s in surnames if s in self.hand_written]
            if hand and self.rng.random() < 0.6:  # prefer the hand-written surnames for the people fans meet
                surnames = hand
        firsts = [f for f in self.first_pool[nationality] if f.lower() not in self.protected_first]
        fresh = [f for f in firsts if self.first_count[f.lower()] == 0]
        if not surnames:
            raise RuntimeError(f"surname pool for {nationality} is exhausted")
        if unique_first and not fresh:
            raise RuntimeError(f"first-name pool for {nationality} is exhausted")
        if not fresh:  # least-used first name, never a protected one
            least = min(self.first_count[f.lower()] for f in firsts)
            fresh = [f for f in firsts if self.first_count[f.lower()] == least]
        for _ in range(200):
            first, last = self.rng.choice(fresh), self.rng.choice(surnames)
            if f"{first} {last}".lower() not in self.famous_full:
                break
        else:  # pragma: no cover - only with a broken blocklist
            raise RuntimeError("could not draw a name outside the blocklist")
        self.used_surnames.add(last.lower())
        self.first_count[first.lower()] += 1
        if unique_first:
            self.match_stems.add(last.lower()[:3])
        return first, last
