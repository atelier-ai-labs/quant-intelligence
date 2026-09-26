"""Point-in-time SEC EDGAR research pipeline (free public endpoints only)."""

from .client import FilingRef, SecClient, SecConfigurationError, SecFetchError
from .diff import ChangedParagraph, RiskFactorDiff, diff_paragraphs
from .form4 import Form4ParseError, InsiderTransaction, open_market_purchases, parse_form4
from .risk_factors import RiskFactorExtractionError, extract_item_1a, html_to_text, split_paragraphs

__all__ = ["ChangedParagraph", "FilingRef", "Form4ParseError", "InsiderTransaction", "RiskFactorDiff", "RiskFactorExtractionError", "SecClient", "SecConfigurationError", "SecFetchError", "diff_paragraphs", "extract_item_1a", "html_to_text", "open_market_purchases", "parse_form4", "split_paragraphs"]
