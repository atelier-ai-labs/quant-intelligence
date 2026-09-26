"""LLM (local Ollama) risk-factor signals. Signals are intent-only and must route through RiskGate."""

from .engine import SignalInputs, build_prompt, generate_signal
from .mapping import SignalRouting, route_signal, signal_to_intent
from .ollama import OllamaClient, OllamaError
from .schema import Citation, LLMSignalPayload, RiskSignal, validate_citations

__all__ = ["Citation", "LLMSignalPayload", "OllamaClient", "OllamaError", "RiskSignal", "SignalInputs", "SignalRouting", "build_prompt", "generate_signal", "route_signal", "signal_to_intent", "validate_citations"]
