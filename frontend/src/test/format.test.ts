import { describe, expect, it } from "vitest";
import { decimal, money, percent } from "../utils/format";

describe("formatting", () => {
  it("formats research metrics without hiding null values", () => {
    expect(money(10000)).toBe("$10,000");
    expect(percent(0.125)).toBe("12.50%");
    expect(decimal(1.234)).toBe("1.23");
    expect(percent(null)).toBe("—");
  });
});
