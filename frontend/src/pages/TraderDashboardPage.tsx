import { useCallback, useEffect, useState } from "react";
import {
  fetchTraderDecision,
  fetchTraderDecisions,
  fetchTraderPortfolio,
  fetchTraderStatus,
} from "../data/trader";
import type {
  DecisionSummary,
  TraderPortfolio,
  TraderStatus,
  TradingDecision,
} from "../types/trader";
import { money } from "../utils/format";

const timeLabel = (value: string | null | undefined) =>
  value
    ? new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";

export function TraderDashboardPage() {
  const [status, setStatus] = useState<TraderStatus | null>(null);
  const [portfolio, setPortfolio] = useState<TraderPortfolio | null>(null);
  const [decisions, setDecisions] = useState<DecisionSummary[]>([]);
  const [selected, setSelected] = useState<TradingDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextPortfolio, nextDecisions] = await Promise.all([
        fetchTraderStatus(),
        fetchTraderPortfolio(),
        fetchTraderDecisions(),
      ]);
      setStatus(nextStatus);
      setPortfolio(nextPortfolio);
      setDecisions(nextDecisions);
      setError(null);
      if (selected) setSelected(await fetchTraderDecision(selected.cycle_id));
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to load trader operations",
      );
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  if (loading)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">Paper trader</span>
          <h1>Loading operations</h1>
          <div className="loading-bar" />
        </div>
      </main>
    );
  if (error && !status)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">Trader operations unavailable</span>
          <h1>Could not load paper trader</h1>
          <p>{error}</p>
          <p className="muted">
            Confirm the FastAPI service is running and its local paper-trader
            state directory is readable.
          </p>
        </div>
      </main>
    );

  const latest = decisions[0];
  const positionCount = portfolio?.positions.length ?? 0;
  return (
    <main className="page trader-page">
      <section className="paper-banner">
        <span className="eyebrow">Execution environment</span>
        <strong>PAPER TRADING</strong>
        <span>Read-only operations view · no live orders</span>
      </section>
      {error && <div className="inline-error">Refresh issue: {error}</div>}
      <header className="experiment-header trader-header">
        <div>
          <div className="eyebrow">Atelier AI · Quant Intelligence</div>
          <h1>Trader Operations</h1>
          <div className="experiment-subtitle">
            <span
              className={
                status?.state === "running"
                  ? "status-dot healthy"
                  : "status-dot stopped"
              }
            >
              {status?.state?.toUpperCase() ?? "UNKNOWN"}
            </span>
            <span>Mode: PAPER</span>
          </div>
        </div>
        <div className="header-meta">
          <span>Last cycle</span>
          <strong>{timeLabel(status?.last_cycle_timestamp)}</strong>
          <span>Outcome</span>
          <strong>{status?.last_cycle_outcome ?? "—"}</strong>
        </div>
      </header>
      <section className="status-strip">
        <StatusItem
          label="Trader"
          value={status?.state?.toUpperCase() ?? "UNKNOWN"}
          tone={status?.state === "running" ? "positive" : "neutral"}
        />
        <StatusItem
          label="Data timestamp"
          value={timeLabel(status?.most_recent_market_data_timestamp)}
        />
        <StatusItem
          label="Last error"
          value={status?.last_error ?? "None recorded"}
          tone={status?.last_error ? "negative" : "positive"}
        />
        <StatusItem label="Open positions" value={String(positionCount)} />
      </section>
      <section className="ops-grid">
        <div className="panel ops-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Paper account</span>
              <h2>Portfolio</h2>
            </div>
          </div>
          <div className="portfolio-metrics">
            <Metric label="Equity" value={money(portfolio?.equity)} />
            <Metric label="Cash" value={money(portfolio?.cash)} />
            <Metric
              label="Costs paid"
              value={money(portfolio?.transaction_costs_paid)}
            />
          </div>
          {portfolio?.positions.length ? (
            <div className="table-scroll">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Quantity</th>
                    <th>Average price</th>
                    <th>Market price</th>
                    <th>Market value</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolio.positions.map((position) => (
                    <tr key={position.symbol}>
                      <td>{position.symbol}</td>
                      <td>{position.quantity}</td>
                      <td>{money(position.average_price)}</td>
                      <td>{money(position.market_price)}</td>
                      <td>{money(position.market_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty-state">No open positions.</div>
          )}
          {portfolio?.valuation_note && (
            <p className="data-note">{portfolio.valuation_note}</p>
          )}
        </div>
        <div className="panel ops-panel latest-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Most recent evidence</span>
              <h2>Latest decision</h2>
            </div>
          </div>
          {latest ? (
            <DecisionCard
              decision={latest}
              onSelect={() =>
                void fetchTraderDecision(latest.cycle_id).then(setSelected)
              }
            />
          ) : (
            <div className="empty-state">
              The trader has not persisted a decision yet.
            </div>
          )}
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading activity-heading">
          <div>
            <span className="eyebrow">Audit trail</span>
            <h2>Recent activity</h2>
          </div>
          <span className="muted">Polling every 15 seconds</span>
        </div>
        {decisions.length ? (
          <div className="table-scroll">
            <table className="ops-table activity-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Signal</th>
                  <th>Risk</th>
                  <th>Order</th>
                  <th>Fill</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((decision) => (
                  <tr
                    key={decision.cycle_id}
                    className="clickable-row"
                    onClick={() =>
                      void fetchTraderDecision(decision.cycle_id).then(
                        setSelected,
                      )
                    }
                  >
                    <td>{timeLabel(decision.timestamp)}</td>
                    <td>{decision.symbol}</td>
                    <td className={`signal ${decision.signal.toLowerCase()}`}>
                      {decision.signal}
                    </td>
                    <td>{decision.risk.approved ? "PASS" : "REJECT"}</td>
                    <td>
                      {decision.order
                        ? `${decision.order.quantity} ${decision.order.side}`
                        : "—"}
                    </td>
                    <td>{decision.fill ? money(decision.fill.price) : "—"}</td>
                    <td>{decision.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">
            No decisions recorded. HOLD-only and never-run states are safe and
            expected.
          </div>
        )}
      </section>
      {selected && (
        <DecisionDetail decision={selected} onClose={() => setSelected(null)} />
      )}
      <footer className="page-footer">
        Operational observation only · Quant Intelligence does not place live
        orders.
      </footer>
    </main>
  );
}

function StatusItem({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div>
      <span>{label}</span>
      <strong className={tone ?? ""}>{value}</strong>
    </div>
  );
}
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ops-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function DecisionCard({
  decision,
  onSelect,
}: {
  decision: DecisionSummary;
  onSelect: () => void;
}) {
  return (
    <button className="decision-card" onClick={onSelect}>
      <div className="decision-card-top">
        <span className="symbol-chip">{decision.symbol}</span>
        <strong className={`signal ${decision.signal.toLowerCase()}`}>
          {decision.signal}
        </strong>
        <span className="decision-outcome">{decision.outcome}</span>
      </div>
      <p>{decision.risk.reason}</p>
      <dl>
        <div>
          <dt>Time</dt>
          <dd>{timeLabel(decision.timestamp)}</dd>
        </div>
        <div>
          <dt>Cycle</dt>
          <dd>{decision.cycle_id.slice(0, 12)}…</dd>
        </div>
        <div>
          <dt>Order</dt>
          <dd>
            {decision.order
              ? `${decision.order.side} · ${decision.order.quantity}`
              : "No order"}
          </dd>
        </div>
      </dl>
      <span className="detail-link">Open audit detail →</span>
    </button>
  );
}
function DecisionDetail({
  decision,
  onClose,
}: {
  decision: TradingDecision;
  onClose: () => void;
}) {
  return (
    <div className="detail-panel panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Canonical persisted record</span>
          <h2>Decision detail</h2>
        </div>
        <button className="close-button" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="detail-meta">
        <span>
          Cycle <strong>{decision.cycle_id}</strong>
        </span>
        <span>
          Strategy <strong>{decision.strategy}</strong>
        </span>
        <span>
          Data <strong>{timeLabel(decision.data_timestamp)}</strong>
        </span>
      </div>
      <div className="decision-flow">
        <FlowStep
          label="Signal"
          value={`${decision.signal} · ${decision.signal_reason}`}
        />
        <FlowStep
          label="Proposed order"
          value={
            decision.proposed_order
              ? `${decision.proposed_order.side} ${decision.proposed_order.quantity} ${decision.proposed_order.symbol}`
              : "None"
          }
        />
        <FlowStep
          label="Risk decision"
          value={`${decision.risk_decision.approved ? "Approved" : "Rejected"} · ${decision.risk_decision.reason}`}
        />
        <FlowStep
          label="Submitted order"
          value={
            decision.submitted_order
              ? `${decision.submitted_order.status} · ${decision.submitted_order.order_id}`
              : "None"
          }
        />
        <FlowStep
          label="Fill"
          value={
            decision.fill
              ? `${decision.fill.quantity} @ ${money(decision.fill.price)} · cost ${money(decision.fill.transaction_cost)}`
              : "None"
          }
        />
      </div>
      {decision.error && <div className="inline-error">{decision.error}</div>}
      <div className="snapshot-grid">
        <Snapshot
          label="Portfolio before"
          snapshot={decision.portfolio_before}
        />
        <Snapshot label="Portfolio after" snapshot={decision.portfolio_after} />
      </div>
    </div>
  );
}
function FlowStep({ label, value }: { label: string; value: string }) {
  return (
    <div className="flow-step">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function Snapshot({
  label,
  snapshot,
}: {
  label: string;
  snapshot: TradingDecision["portfolio_before"];
}) {
  return (
    <div className="snapshot">
      <span>{label}</span>
      <strong>{money(snapshot?.equity)}</strong>
      <small>
        Cash {money(snapshot?.cash)} · {snapshot?.positions.length ?? 0}{" "}
        positions
      </small>
    </div>
  );
}
