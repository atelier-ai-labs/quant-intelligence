"""Pydantic contracts for LLM output and the validated signal handed to the trading layer."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Direction = Literal["bullish", "bearish", "neutral"]
ACCESSION_PATTERN = r"^\d{10}-\d{2}-\d{6}$"
_WS = re.compile(r"\s+")


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    accession_no: str = Field(pattern=ACCESSION_PATTERN)
    snippet: str = Field(min_length=12, max_length=600)


class LLMSignalPayload(BaseModel):
    """Exactly what the model must return (also sent to Ollama as the JSON-schema `format`)."""
    model_config = ConfigDict(extra="forbid")
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _directional_needs_citations(self) -> "LLMSignalPayload":
        if self.direction != "neutral" and not self.citations: raise ValueError("bullish/bearish signals require at least one citation")
        return self


class RiskSignal(BaseModel):
    """Validated signal. status == "no_signal" is the fail-closed result (always neutral, confidence 0)."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    ticker: str = Field(min_length=1, max_length=10)
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    citations: tuple[Citation, ...] = ()
    as_of: datetime
    model: str
    status: Literal["ok", "no_signal"]
    reason: str = ""

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None: raise ValueError("as_of must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _no_signal_is_neutral(self) -> "RiskSignal":
        if self.status == "no_signal" and (self.direction != "neutral" or self.confidence != 0.0): raise ValueError("no_signal must be neutral with zero confidence")
        return self

    @classmethod
    def no_signal(cls, ticker: str, as_of: datetime, model: str, reason: str) -> "RiskSignal":
        return cls(ticker=ticker, direction="neutral", confidence=0.0, rationale="", citations=(), as_of=as_of, model=model, status="no_signal", reason=reason)


def normalize(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return _WS.sub(" ", text).strip()


def validate_citations(payload: LLMSignalPayload, sources: dict[str, str]) -> str | None:
    """Return None if every citation is grounded, else a rejection reason.

    Each citation's accession must be one of the provided filings, and its snippet must appear
    verbatim (modulo whitespace/curly quotes) in the text supplied for that accession.
    """
    normalized = {acc: normalize(text) for acc, text in sources.items()}
    for i, citation in enumerate(payload.citations):
        if citation.accession_no not in normalized: return f"citation {i} references unknown accession {citation.accession_no}"
        if normalize(citation.snippet) not in normalized[citation.accession_no]: return f"citation {i} snippet not found in source text for {citation.accession_no}"
    return None
