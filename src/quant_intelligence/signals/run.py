"""End-to-end, research-only CLI: EDGAR -> Item 1A diff (+ Form 4 buys) -> local Ollama -> signal -> OrderIntent -> RiskGate.

    python -m quant_intelligence.signals.run --ticker AAPL
    python -m quant_intelligence.signals.run --universe --all-events

Never submits orders: the output is a RiskGate decision evaluated against a hypothetical paper snapshot.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from quant_intelligence.edgar.client import SecClient, SecConfigurationError, SecFetchError
from quant_intelligence.trading.models import PortfolioSnapshot
from quant_intelligence.trading.risk import RiskConfig, RiskGate

from .engine import generate_signal
from .mapping import route_signal
from .ollama import OllamaClient, OllamaError
from .pipeline import DEFAULT_CONFIG, PipelineError, build_event_inputs, build_inputs, load_universe

DEFAULT_REPLAY_LOG = "data/signals/replay.jsonl"
log = logging.getLogger(__name__)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone offset, e.g. 2025-12-31T00:00:00+00:00")
    return parsed


def _route(signal, args, universe, ticker: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    snapshot = PortfolioSnapshot(now, args.paper_cash, (), 0.0, args.paper_cash, 0.0)
    gate = RiskGate(
        RiskConfig(
            max_position_allocation=float(os.environ.get("QI_MAX_POSITION_ALLOCATION", 0.25)),
            max_order_notional=float(os.environ.get("QI_MAX_ORDER_NOTIONAL", 2500)),
        )
    )
    price = args.price
    result: dict[str, Any] = {}
    if price is None:
        try:
            from quant_intelligence.event_study.prices import PriceStore, as_of_reference_price

            price = as_of_reference_price(
                PriceStore(os.environ.get("QI_PRICE_CACHE_DIR", "data/prices"), allow_network=False),
                ticker,
                signal.as_of,
            )
            result["reference_price"] = price
            result["reference_price_source"] = "yahoo-cache-as-of-entry-close"
        except Exception as exc:  # noqa: BLE001
            result["reference_price_error"] = str(exc)
    routing = route_signal(
        signal, gate, snapshot, price, quantity=universe.order_quantity, min_confidence=universe.min_confidence
    )
    result["intent"] = (
        None
        if routing.intent is None
        else {
            "symbol": routing.intent.symbol,
            "side": routing.intent.side.value,
            "quantity": routing.intent.quantity,
            "reason": routing.intent.reason,
        }
    )
    result["risk_decision"] = {"approved": routing.decision.approved, "reason": routing.decision.reason}
    result["note"] = "research only: no broker was called; an approved decision would still need the existing execution layer"
    return result


def run_ticker(ticker: str, args: argparse.Namespace, sec: SecClient, ollama: OllamaClient, universe) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        if args.all_events:
            pairs = build_event_inputs(
                sec,
                ticker,
                cutoff=args.as_of,
                lookback_days=universe.form4_lookback_days,
                max_form4=universe.max_form4_filings,
                max_10k_pairs=universe.max_10k_pairs,
                include_10q=universe.include_10q,
                max_10q_events=universe.max_10q_events,
                min_10k_history=universe.min_10k_history,
                min_10q_history=universe.min_10q_history,
            )
        else:
            pairs = [build_inputs(sec, ticker, cutoff=args.as_of, lookback_days=universe.form4_lookback_days, max_form4=universe.max_form4_filings)]
    except (PipelineError, SecFetchError) as exc:
        return [{"ticker": ticker.upper(), "status": "no_signal", "reason": f"pipeline failed closed: {exc}", "intent": None, "risk_decision": None}]

    for inputs, report in pairs:
        result: dict[str, Any] = {"ticker": ticker.upper(), "pipeline": report.__dict__}
        try:
            signal = generate_signal(inputs, ollama, args.replay_log, prefer_replay=not args.force_ollama)
        except OllamaError as exc:
            # Fail closed for this event; continue other events.
            result.update(status="no_signal", reason=f"ollama unavailable: {exc}", intent=None, risk_decision=None)
            results.append(result)
            continue
        result["signal"] = signal.model_dump(mode="json")
        result.update(_route(signal, args, universe, ticker))
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quant_intelligence.signals.run", description=__doc__.split("\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--ticker")
    target.add_argument("--universe", action="store_true", help="run every ticker in the config file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--as-of", type=_parse_time, default=None, help="point-in-time cutoff (default: now); only filings accepted on/before it are used")
    parser.add_argument("--price", type=float, default=None, help="reference price for the RiskGate check (omitted => gate not satisfied, fail closed)")
    parser.add_argument("--paper-cash", type=float, default=10_000.0, help="hypothetical paper cash for the RiskGate snapshot")
    parser.add_argument("--replay-log", default=os.environ.get("QI_SIGNAL_REPLAY_LOG", DEFAULT_REPLAY_LOG))
    parser.add_argument("--all-events", action="store_true", help="expand history: consecutive 10-K pairs + 10-Q Item 1A diffs")
    parser.add_argument("--force-ollama", action="store_true", help="ignore replay cache and call Ollama for every event")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    universe = load_universe(args.config)
    try:
        sec = SecClient(os.environ.get("SEC_USER_AGENT"), os.environ.get("QI_EDGAR_CACHE_DIR", "data/edgar"), max_per_second=universe.requests_per_second)
        ollama = OllamaClient.from_env()
    except (SecConfigurationError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    tickers = universe.tickers if args.universe else (args.ticker.upper(),)
    for ticker in tickers:
        for result in run_ticker(ticker, args, sec, ollama, universe):
            print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
