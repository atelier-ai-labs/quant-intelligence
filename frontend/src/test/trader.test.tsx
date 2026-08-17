import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TraderDashboardPage } from "../pages/TraderDashboardPage";

const summary = {
  cycle_id: "cycle-1234567890",
  timestamp: "2020-01-04T12:00:00+00:00",
  symbol: "SYNTH",
  signal: "BUY",
  outcome: "EXECUTED",
  risk: { approved: true, reason: "order accepted" },
  order: { side: "BUY", quantity: 76, status: "FILLED" },
  fill: { price: 13, quantity: 76, transaction_cost: 0 },
  error: null,
};

const detail = {
  ...summary,
  strategy: "sma_trend",
  strategy_parameters: { window: 3 },
  data_timestamp: "2020-01-04T00:00:00+00:00",
  signal_reason: "SMA desired position is LONG",
  proposed_order: {
    symbol: "SYNTH",
    side: "BUY",
    quantity: 76,
    order_type: "MARKET",
    asset_type: "EQUITY",
    reason: "SMA desired position is LONG",
  },
  risk_decision: { approved: true, reason: "order accepted", intent: null },
  submitted_order: {
    order_id: "order-1",
    intent: {
      symbol: "SYNTH",
      side: "BUY",
      quantity: 76,
      order_type: "MARKET",
      asset_type: "EQUITY",
      reason: "SMA desired position is LONG",
    },
    submitted_at: "2020-01-04T12:00:00+00:00",
    status: "FILLED",
  },
  fill: {
    order_id: "order-1",
    symbol: "SYNTH",
    side: "BUY",
    quantity: 76,
    price: 13,
    gross_notional: 988,
    transaction_cost: 0,
    filled_at: "2020-01-04T12:00:00+00:00",
  },
  portfolio_before: {
    timestamp: "2020-01-04T12:00:00+00:00",
    cash: 1000,
    positions: [],
    asset_value: 0,
    equity: 1000,
    transaction_costs_paid: 0,
  },
  portfolio_after: {
    timestamp: "2020-01-04T12:00:00+00:00",
    cash: 12,
    positions: [{ symbol: "SYNTH", quantity: 76, average_price: 13 }],
    asset_value: 988,
    equity: 1000,
    transaction_costs_paid: 0,
  },
};

function response(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body });
}

describe("trader dashboard", () => {
  it("renders paper mode, status, portfolio, activity, and audit detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.endsWith("/status"))
          return response({
            available: true,
            mode: "paper",
            state: "running",
            last_cycle_id: summary.cycle_id,
            last_cycle_timestamp: summary.timestamp,
            last_cycle_outcome: "EXECUTED",
            current_equity: 1000,
            current_cash: 12,
            current_positions: [
              { symbol: "SYNTH", quantity: 76, average_price: 13 },
            ],
          });
        if (url.endsWith("/portfolio"))
          return response({
            available: true,
            mode: "paper",
            equity: 1000,
            cash: 12,
            transaction_costs_paid: 0,
            positions: [
              {
                symbol: "SYNTH",
                quantity: 76,
                average_price: 13,
                market_price: null,
                market_value: null,
              },
            ],
            valuation_note: "Prices omitted.",
          });
        if (url.includes("/decisions/")) return response(detail);
        return response([summary]);
      }),
    );
    render(<TraderDashboardPage />);
    expect(screen.getByText("Loading operations")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("PAPER TRADING")).toBeInTheDocument(),
    );
    expect(screen.getAllByText("RUNNING").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SYNTH").length).toBeGreaterThan(0);
    expect(screen.getByText("76")).toBeInTheDocument();
    expect(screen.getAllByText("EXECUTED").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("Open audit detail →"));
    await waitFor(() =>
      expect(screen.getByText("Decision detail")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/SMA desired position is LONG/),
    ).toBeInTheDocument();
  });

  it("renders an unavailable state when the API cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("API offline")));
    render(<TraderDashboardPage />);
    await waitFor(() =>
      expect(
        screen.getByText("Could not load paper trader"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("API offline")).toBeInTheDocument();
  });
});
