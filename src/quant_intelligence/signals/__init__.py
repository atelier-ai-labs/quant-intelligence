"""LLM (local Ollama) risk-factor signals. Signals are intent-only and must route through RiskGate."""

from .engine import PROMPT_VERSION, SignalInputs, build_prompt, generate_signal
from .hardening import (
    apply_hardening,
    has_insider_buy_cluster,
    validate_citations_against_diff,
)
from .mapping import SignalRouting, route_signal, signal_to_intent
from .ollama import OllamaClient, OllamaError
from .schema import Citation, LLMSignalPayload, RiskSignal, validate_citations

__all__ = [
    "Citation",
    "LLMSignalPayload",
    "OllamaClient",
    "OllamaError",
    "PROMPT_VERSION",
    "RiskSignal",
    "SignalInputs",
    "SignalRouting",
    "apply_hardening",
    "build_prompt",
    "generate_signal",
    "has_insider_buy_cluster",
    "route_signal",
    "signal_to_intent",
    "validate_citations",
    "validate_citations_against_diff",
]
