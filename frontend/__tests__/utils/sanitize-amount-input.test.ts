import { describe, expect, it } from "vitest";
import { sanitizeAmountInput } from "#/utils/sanitize-amount-input";

describe("sanitizeAmountInput", () => {
  it.each([
    ["250", "250"],
    ["ab1e2-.5", "12.5"],
    ["1.2.3", "1.23"],
    ["$1,000", "1000"],
    [".5", "0.5"],
    [".", "0."],
    ["100.00000", "100.00"],
    ["12.345", "12.34"],
    ["abc", ""],
  ])("turns %j into %j", (input, expected) => {
    expect(sanitizeAmountInput(input)).toBe(expected);
  });
});
