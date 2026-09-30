"""CLI: point-in-time event study over EDGAR/Ollama signals.

Defaults to replay mode (no Ollama). Live mode regenerates signals then studies them.

    python -m quant_intelligence.event_study.run --replay data/signals/replay.jsonl
    make event-study
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from quant_intelligence.signals.pipeline import DEFAULT_CONFIG, load_universe
from quant_intelligence.signals.schema import RiskSignal

from .prices import PriceStore, as_of_reference_price
from .replay import DEFAULT_REPLAY_LOG, load_replay_signals
from .study import DEFAULT_HORIZONS as HORIZONS, EventStudyConfig, run_event_study


def _write_outputs(result, output_dir: Path, *, write_markdown: bool) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "event_study.json"
    md_path = output_dir / "event_study.md"
    payload = result.to_dict()
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    paths = {"json": str(json_path)}
    if write_markdown:
        lines = [
            "# Event study report",
            "",
            f"- mode: `{result.mode}`",
            f"- price_source: `{result.price_source}`",
            f"- gate: **{result.gate.get('decision')}**",
            "",
            "## Summary",
            "",
            result.markdown_summary(),
            "",
            "## Gate rationale",
            "",
        ]
        for item in result.gate.get("rationale", []):
            lines.append(f"- {item}")
        lines.extend(["", "## Notes", ""])
        for note in result.notes:
            lines.append(f"- {note}")
        lines.append("")
        md_path.write_text("\n".join(lines), encoding="utf-8")
        paths["markdown"] = str(md_path)
    return paths


def _run_live(args: argparse.Namespace) -> list[RiskSignal]:
    """Generate signals via the EDGAR→Ollama pipeline (expanded multi-event by default), then return RiskSignals."""
    from quant_intelligence.edgar.client import SecClient, SecConfigurationError, SecFetchError
    from quant_intelligence.signals.engine import generate_signal
    from quant_intelligence.signals.ollama import OllamaClient, OllamaError
    from quant_intelligence.signals.pipeline import PipelineError, build_event_inputs, build_inputs

    universe = load_universe(args.config)
    try:
        sec = SecClient(
            os.environ.get("SEC_USER_AGENT"),
            os.environ.get("QI_EDGAR_CACHE_DIR", "data/edgar"),
            max_per_second=universe.requests_per_second,
        )
        ollama = OllamaClient.from_env()
    except (SecConfigurationError, ValueError) as exc:
        raise SystemExit(f"configuration error: {exc}") from exc

    tickers = list(universe.tickers) if args.universe else [args.ticker.upper()]
    store = PriceStore(args.price_cache, allow_network=args.allow_network)
    signals: list[RiskSignal] = []
    prefer_replay = not getattr(args, "force_ollama", False)
    all_events = getattr(args, "all_events", True)
    for ticker in tickers:
        try:
            if all_events:
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
                pairs = [
                    build_inputs(
                        sec,
                        ticker,
                        cutoff=args.as_of,
                        lookback_days=universe.form4_lookback_days,
                        max_form4=universe.max_form4_filings,
                    )
                ]
        except (PipelineError, SecFetchError) as exc:
            logging.warning("pipeline failed for %s: %s", ticker, exc)
            continue
        for inputs, report in pairs:
            try:
                signal = generate_signal(inputs, ollama, args.replay_log, prefer_replay=prefer_replay)
            except OllamaError as exc:
                logging.warning("ollama unavailable for %s %s: %s (fail closed)", ticker, report.event_kind, exc)
                continue
            try:
                ref = as_of_reference_price(store, ticker, signal.as_of)
                logging.info("as-of reference price for %s @ %s: %.4f", ticker, signal.as_of.date(), ref)
            except Exception as exc:  # noqa: BLE001
                logging.warning("no reference price for %s: %s", ticker, exc)
            signals.append(signal)
    return signals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quant_intelligence.event_study.run", description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", nargs="?", const=str(DEFAULT_REPLAY_LOG), default=str(DEFAULT_REPLAY_LOG),
                      help="replay JSONL path (default: data/signals/replay.jsonl); no Ollama")
    mode.add_argument("--live", action="store_true", help="generate signals with Ollama then study them")
    parser.add_argument("--ticker", help="with --live: single ticker")
    parser.add_argument("--universe", action="store_true", help="with --live: every configured ticker")
    parser.add_argument("--all-events", action="store_true", default=True,
                        help="with --live: consecutive 10-K pairs + 10-Q Item 1A diffs (default on)")
    parser.add_argument("--latest-only", action="store_true", help="with --live: only the latest 10-K pair per ticker")
    parser.add_argument("--force-ollama", action="store_true", help="ignore replay cache; call Ollama for every event")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--as-of", default=None, help="live mode point-in-time cutoff (ISO-8601 with tz)")
    parser.add_argument("--replay-log", default=os.environ.get("QI_SIGNAL_REPLAY_LOG", str(DEFAULT_REPLAY_LOG)))
    parser.add_argument("--price-cache", default=os.environ.get("QI_PRICE_CACHE_DIR", "data/prices"))
    parser.add_argument("--allow-network", action="store_true", help="fetch missing prices from Yahoo Finance")
    parser.add_argument("--min-confidence", type=float, default=0.6)
    parser.add_argument("--random-draws", type=int, default=500)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--include-neutral", action="store_true", help="include non-actionable signals in cohort")
    parser.add_argument("--output-dir", default="data/event_study")
    parser.add_argument("--artifact", default=None, help="also write a committed sample under this path (e.g. docs/artifacts)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if getattr(args, "latest_only", False):
        args.all_events = False

    if args.live:
        if not args.ticker and not args.universe:
            parser.error("--live requires --ticker or --universe")
        if args.as_of:
            parsed = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parser.error("--as-of must include a timezone offset")
            args.as_of = parsed
        signals = _run_live(args)
        mode_name = "live"
    else:
        signals = load_replay_signals(args.replay)
        mode_name = "replay"

    if not signals:
        print("no signals to study", file=sys.stderr)
        return 1

    store = PriceStore(args.price_cache, allow_network=args.allow_network)
    if args.allow_network:
        for signal in signals:
            try:
                store.load(signal.ticker)
            except Exception as exc:  # noqa: BLE001
                logging.warning("price load failed for %s: %s", signal.ticker, exc)

    config = EventStudyConfig(
        horizons=dict(HORIZONS),
        min_confidence=args.min_confidence,
        random_draws=args.random_draws,
        random_seed=args.random_seed,
        actionable_only=not args.include_neutral,
    )
    result = run_event_study(
        signals,
        store,
        config=config,
        mode=mode_name,
        price_source="yahoo-finance-cache" if Path(args.price_cache).exists() else "unavailable",
    )
    paths = _write_outputs(result, Path(args.output_dir), write_markdown=True)
    if args.artifact:
        art = _write_outputs(result, Path(args.artifact), write_markdown=True)
        paths["artifact_json"] = art["json"]
        paths["artifact_markdown"] = art.get("markdown", "")

    summary: dict[str, Any] = {
        "mode": result.mode,
        "price_source": result.price_source,
        "n_signals": len(signals),
        "n_events_priced": sum(1 for e in result.events if e.error is None),
        "n_actionable": sum(
            1
            for e in result.events
            if e.error is None
            and e.status == "ok"
            and e.direction in {"bullish", "bearish"}
            and e.confidence >= config.min_confidence
        ),
        "gate": result.gate,
        "outputs": paths,
        "markdown_summary": result.markdown_summary(),
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
