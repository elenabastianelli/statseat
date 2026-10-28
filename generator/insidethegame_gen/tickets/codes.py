"""Ticket codes: `GNT-<match>-<tier letter>-XXXX-XXXX`, with a check character.

Example: `GNT-SYN001-D-7KQ2-M9XA`.

- `GNT` is the fixed app prefix; `SYN001` is the match id without the dash; the tier letter is
  S (silver), G (gold) or D (demo; the hackathon judges use DEMO codes).
- The last 8 characters use the Crockford base32 alphabet, which leaves out the look-alike
  letters I, L, O and U. When reading a code, O is taken as 0 and I or L as 1, as Crockford
  recommends, so a fan who types an O for a zero still gets in.
- The 8th character is a check character: Luhn mod 32 over everything before it (app, match,
  tier letter and the first 7 characters). It catches every single-character typo and almost
  every swap of two neighbouring characters.
- Codes are derived from the universe seed, the match id, the tier and the ticket number with
  SHA-256, so the same inputs always give the same codes. They are demo credentials derived
  from public inputs, not secrets (see the tickets README).
- The server keeps only `code_sha256`: the SHA-256 of the normalised code (upper case, with
  dashes, UTF-8), never the plain code.
"""

from __future__ import annotations

import hashlib
import re

PREFIX = "GNT"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
VALUE = {ch: i for i, ch in enumerate(ALPHABET)}
ALIASES = {"O": "0", "I": "1", "L": "1"}  # Crockford decoding of look-alike characters
TIER_LETTER = {"silver": "S", "gold": "G", "demo": "D"}
LETTER_TIER = {letter: tier for tier, letter in TIER_LETTER.items()}
PAYLOAD_LENGTH = 7  # random characters; with the check character they make XXXX-XXXX
SEPARATORS = re.compile(r"[\s\-_.]+")
MATCH_ID = re.compile(r"([A-Z]+)-?(\d+)")
CODE_PATTERN = r"^GNT-[A-Z]+[0-9]+-[SGD]-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$"


def match_token(match_id: str) -> str:
    """`SYN-001` -> `SYN001` (the match part of a code)."""
    found = MATCH_ID.fullmatch(match_id.upper())
    if not found:
        raise ValueError(f"match id {match_id!r} is not letters-digits, e.g. SYN-001")
    token = found.group(1) + found.group(2)
    if any(ch not in VALUE for ch in token):
        raise ValueError(f"match id {match_id!r} uses a letter outside the code alphabet ({ALPHABET})")
    return token


def match_id_from_token(token: str) -> str:
    """`SYN001` -> `SYN-001`."""
    found = re.fullmatch(r"([A-Z]+)(\d+)", token)
    if not found:
        raise ValueError(f"bad match token {token!r}")
    return f"{found.group(1)}-{found.group(2)}"


def check_character(body: str) -> str:
    """Luhn mod 32 check character of `body` (characters of ALPHABET only)."""
    factor, total = 2, 0
    for ch in reversed(body):
        addend = factor * VALUE[ch]
        total += addend // 32 + addend % 32
        factor = 3 - factor  # 2, 1, 2, 1, ... from the right
    return ALPHABET[(32 - total % 32) % 32]


def format_code(token: str, letter: str, payload: str) -> str:
    """Canonical form of a code from its parts; `payload` is the 7 characters before the check."""
    tail = payload + check_character(PREFIX + token + letter + payload)
    return f"{PREFIX}-{token}-{letter}-{tail[:4]}-{tail[4:]}"


def make_code(seed: int, match_id: str, tier: str, index: int, attempt: int = 0) -> str:
    """The code of ticket `index` (1-based) of `tier` for `match_id`. Deterministic.

    `attempt` > 0 gives another code for the same ticket; the generator uses it only if two
    tickets would otherwise share a code (with 32^7 combinations this practically never happens).
    """
    if tier not in TIER_LETTER:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIER_LETTER)}")
    digest = hashlib.sha256(f"gantry-ticket:{seed}:{match_id}:{tier}:{index}:{attempt}".encode()).digest()
    number = int.from_bytes(digest, "big")
    payload = "".join(ALPHABET[(number >> (5 * i)) & 31] for i in range(PAYLOAD_LENGTH))
    return format_code(match_token(match_id), TIER_LETTER[tier], payload)


def normalise_code(code: str) -> str | None:
    """Canonical form (`GNT-SYN001-G-XXXX-XXXX`) of a typed or scanned code, or None if invalid.

    Accepts lower case, spaces, missing or extra dashes, and O for 0 or I/L for 1 in the
    last 8 characters. Rejects anything with a wrong prefix, tier letter or check character.
    """
    if not isinstance(code, str):
        return None
    compact = SEPARATORS.sub("", code.strip().upper())
    if len(compact) < len(PREFIX) + 2 + 1 + 8 or not compact.startswith(PREFIX):
        return None
    token, letter, tail = compact[len(PREFIX):-9], compact[-9], compact[-8:]
    tail = "".join(ALIASES.get(ch, ch) for ch in tail)
    if letter not in LETTER_TIER or not re.fullmatch(r"[A-Z]+\d+", token):
        return None
    if any(ch not in VALUE for ch in token + tail):
        return None
    if check_character(PREFIX + token + letter + tail[:-1]) != tail[-1]:
        return None
    return f"{PREFIX}-{token}-{letter}-{tail[:4]}-{tail[4:]}"


def validate_code(code: str) -> tuple[bool, str | None, str | None]:
    """(ok, tier, match_id) for a typed or scanned code; (False, None, None) if it is not valid.

    This checks the format and the check character only. Whether the ticket exists is decided by
    looking up `code_sha256(code)` in the registry (`tickets.json`).
    """
    canonical = normalise_code(code)
    if canonical is None:
        return False, None, None
    _, token, letter, _, _ = canonical.split("-")
    return True, LETTER_TIER[letter], match_id_from_token(token)


def code_sha256(code: str) -> str:
    """SHA-256 (hex) of the normalised code: the only form of a code that is stored."""
    canonical = normalise_code(code)
    if canonical is None:
        raise ValueError("not a valid ticket code")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
