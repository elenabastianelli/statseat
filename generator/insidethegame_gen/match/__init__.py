"""Match generator (Tactician v0.1): scripted key moments, Markov possession chains, role mean
positions, ball physics and measurement noise -> DFL-like events and 25 Hz tracking."""

from .api import MATCH_GENERATOR_VERSION, derive_seed, generate_match, generate_replica, realism_report

__all__ = ["generate_match", "generate_replica", "realism_report", "derive_seed", "MATCH_GENERATOR_VERSION"]
