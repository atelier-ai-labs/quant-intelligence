"""Point-in-time event study for EDGAR/Ollama signals vs buy-and-hold and random baselines.

Research / evaluation only. Strategies remain intent-only; RiskGate still decides.
No broker calls. Defaults to replaying logged LLM outputs without contacting Ollama.
"""

from .prices import PriceStore, as_of_reference_price
from .replay import load_replay_signals
from .study import EventStudyConfig, EventStudyResult, run_event_study

__all__ = [
    "EventStudyConfig",
    "EventStudyResult",
    "PriceStore",
    "as_of_reference_price",
    "load_replay_signals",
    "run_event_study",
]
