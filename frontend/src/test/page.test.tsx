import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExperimentDetailPage } from "../pages/ExperimentDetailPage";
import { resultFixture } from "./fixtures";

describe("experiment detail page", () => {
  it("renders loading then experiment metadata and empty trades", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => resultFixture }),
    );
    render(<ExperimentDetailPage resultUrl="/result.json" />);
    expect(screen.getByText("Loading experiment")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("sma-trend")).toBeInTheDocument(),
    );
    expect(screen.getByText("SPY")).toBeInTheDocument();
    expect(
      screen.getByText("No trades were generated in this experiment."),
    ).toBeInTheDocument();
  });
  it("renders a failed request state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404 }),
    );
    render(<ExperimentDetailPage resultUrl="/missing.json" />);
    await waitFor(() =>
      expect(
        screen.getByText("Could not load this result"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/404/)).toBeInTheDocument();
  });
});
