"""Parse SEC Form 4 ownership XML into insider transactions."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


class Form4ParseError(ValueError):
    """The Form 4 document is not parseable ownership XML; callers skip it (fail closed)."""


@dataclass(frozen=True)
class InsiderTransaction:
    issuer_ticker: str
    filer: str
    filer_title: str
    transaction_date: date
    code: str
    acquired_disposed: str
    shares: float
    price: float | None
    accession_no: str
    accepted_at: datetime

    @property
    def is_open_market_purchase(self) -> bool:
        return self.code == "P" and self.acquired_disposed == "A" and self.shares > 0

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["transaction_date"] = self.transaction_date.isoformat(); data["accepted_at"] = self.accepted_at.isoformat()
        return data

    def describe(self) -> str:
        price = "n/a" if self.price is None else f"${self.price:,.2f}"
        return f"Form 4 {self.accession_no}: {self.filer} ({self.filer_title or 'insider'}) open-market purchase of {self.shares:,.0f} shares at {price} on {self.transaction_date.isoformat()}"


def _text(node: ET.Element | None, path: str) -> str:
    if node is None: return ""
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _float(value: str) -> float | None:
    try: return float(value) if value else None
    except ValueError: return None


def parse_form4(document: bytes | str, accession_no: str, accepted_at: datetime) -> list[InsiderTransaction]:
    if accepted_at.tzinfo is None: raise ValueError("accepted_at must be timezone-aware")
    try:
        root = ET.fromstring(document.encode() if isinstance(document, str) else document)
    except ET.ParseError as exc:
        raise Form4ParseError(f"invalid Form 4 XML for {accession_no}: {exc}") from exc
    if root.tag != "ownershipDocument": raise Form4ParseError(f"unexpected root element {root.tag!r} for {accession_no}")
    ticker = _text(root, "issuer/issuerTradingSymbol").upper()
    owners = root.findall("reportingOwner")
    filer = "; ".join(filter(None, (_text(o, "reportingOwnerId/rptOwnerName") for o in owners))) or "unknown"
    rel = owners[0].find("reportingOwnerRelationship") if owners else None
    title = _text(rel, "officerTitle")
    if not title and rel is not None:
        flags = [label for tag, label in (("isDirector", "Director"), ("isTenPercentOwner", "10% owner"), ("isOther", "Other")) if _text(rel, tag) in {"1", "true"}]
        title = ", ".join(flags)
    out = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = _float(_text(txn, "transactionAmounts/transactionShares/value"))
        raw_date = _text(txn, "transactionDate/value")[:10]
        code = _text(txn, "transactionCoding/transactionCode")
        if shares is None or not raw_date or not code: continue  # incomplete row: skip rather than guess
        try: txn_date = date.fromisoformat(raw_date)
        except ValueError: continue
        out.append(InsiderTransaction(ticker, filer, title, txn_date, code, _text(txn, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                                      shares, _float(_text(txn, "transactionAmounts/transactionPricePerShare/value")), accession_no, accepted_at))
    return out


def open_market_purchases(transactions: list[InsiderTransaction]) -> list[InsiderTransaction]:
    return [t for t in transactions if t.is_open_market_purchase]
