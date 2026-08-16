import { describe, expect, it } from "vitest";
import { buildEquityChartData } from "../components/EquityChart";

describe("equity transformation", () => {
  it("joins strategy and benchmark series by date", () => {
    expect(
      buildEquityChartData(
        [
          { date: "2020-01-01", equity: 100 },
          { date: "2020-01-02", equity: 101 },
        ],
        [
          { date: "2020-01-01", equity: 100 },
          { date: "2020-01-02", equity: 102 },
        ],
      ),
    ).toEqual([
      {
        date: "2020-01-01",
        label: "2020-01-01",
        strategy: 100,
        benchmark: 100,
      },
      {
        date: "2020-01-02",
        label: "2020-01-02",
        strategy: 101,
        benchmark: 102,
      },
    ]);
  });
});
