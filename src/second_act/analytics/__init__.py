"""Deterministic tactical analytics over normalized event and snapshot frames.

No model calls: every value is plain, tested Python. ``compute_signals``
returns a ``SignalSet`` of values with stable IDs that agents cite and the
verifier checks.
"""

from second_act.analytics.signals import (
    PassNetwork,
    SignalSet,
    compute_signals,
    make_id,
    pass_network,
)

__all__ = ["PassNetwork", "SignalSet", "compute_signals", "make_id", "pass_network"]
