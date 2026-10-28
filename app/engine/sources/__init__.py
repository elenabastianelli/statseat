"""Match sources selected by MATCH_SOURCE. Every adapter returns the same canonical `MatchData` (see base.py).

| MATCH_SOURCE | Adapter | Frames |
|---|---|---|
| `synthetic:<id>` | `synthetic.load_synthetic`: the synthetic match of S-4, through the data pipeline (default SYN-001) | yes |
| `dfl:<id>` | `dfl.load_dfl`: real DFL data, local development only | yes |
| `fake` | `fake.load_fake`: key events of `app/contract/fake/events.json` | no |

`synthetic` and `dfl` share the mapping of `provider.py`.
"""

from __future__ import annotations

from .base import MatchData, SourceNotAvailable


def load_match(spec: str) -> MatchData:
    spec = (spec or "synthetic:SYN-001").strip()
    kind, _, arg = spec.partition(":")
    if kind == "fake" and not arg:
        from .fake import load_fake
        return load_fake()
    if kind == "dfl" and arg:
        from .dfl import load_dfl
        return load_dfl(arg)
    if kind == "synthetic" and arg:
        from .synthetic import load_synthetic
        return load_synthetic(arg)
    raise ValueError(f"unknown MATCH_SOURCE {spec!r}: use fake, dfl:<match_id> or synthetic:<match_id>")


__all__ = ["MatchData", "SourceNotAvailable", "load_match"]
