import type { PortfolioState } from "../types/result";
import { dateLabel, money, percent } from "../utils/format";

export function DailyStateTable({ states }: { states: PortfolioState[] }) {
  if (!states.length)
    return (
      <div className="empty-state">
        No daily portfolio states are available.
      </div>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Position</th>
            <th>Shares</th>
            <th>Cash</th>
            <th>Asset value</th>
            <th>Equity</th>
            <th>Exposure</th>
            <th>Costs paid</th>
          </tr>
        </thead>
        <tbody>
          {states.map((state) => (
            <tr key={state.date}>
              <td>{dateLabel(state.date)}</td>
              <td>{state.desired_position}</td>
              <td>{state.shares.toLocaleString()}</td>
              <td>{money(state.cash)}</td>
              <td>{money(state.asset_value)}</td>
              <td>{money(state.equity)}</td>
              <td>{percent(state.exposure)}</td>
              <td>{money(state.transaction_costs_paid)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
