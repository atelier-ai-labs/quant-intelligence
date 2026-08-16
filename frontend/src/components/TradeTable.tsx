import type { Trade } from "../types/result";
import { dateLabel, money } from "../utils/format";

export function TradeTable({ trades }: { trades: Trade[] }) {
  if (!trades.length)
    return (
      <div className="empty-state">
        No trades were generated in this experiment.
      </div>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Side</th>
            <th>Quantity</th>
            <th>Execution price</th>
            <th>Gross notional</th>
            <th>Cost</th>
            <th>Cash after</th>
            <th>Equity after</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr key={`${trade.date}-${trade.side}`}>
              <td>{dateLabel(trade.date)}</td>
              <td>
                <span className={`side ${trade.side.toLowerCase()}`}>
                  {trade.side}
                </span>
              </td>
              <td>{trade.quantity.toLocaleString()}</td>
              <td>{money(trade.execution_price)}</td>
              <td>{money(trade.gross_notional)}</td>
              <td>{money(trade.transaction_cost)}</td>
              <td>{money(trade.cash_after)}</td>
              <td>{money(trade.equity_after)}</td>
              <td>{trade.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
