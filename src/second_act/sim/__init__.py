"""Seeded synthetic match generator.

Tactics come from config parameters (pressing, line depth, width, tempo,
channel preference, formation) that can change at half-time and after
substitutions. The generator turns them into events and 1 Hz position
snapshots; no event is scripted.
"""

from second_act.sim.config import MatchConfig, Tactics, load_match_config, load_teams
from second_act.sim.engine import SimResult, simulate

__all__ = ["MatchConfig", "SimResult", "Tactics", "load_match_config", "load_teams", "simulate"]
