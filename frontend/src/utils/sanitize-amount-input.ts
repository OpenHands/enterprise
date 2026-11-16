/**
 * Cleans the text typed into a dollar amount input (e.g. the Budgets inputs).
 * Keeps only digits and the first decimal point, cuts the cents to 2 digits,
 * and adds a leading "0" before a leading point, so a non-empty result always
 * parses with `Number()`.
 * Example: "ab1e2-.5" -> "12.5", ".5" -> "0.5", "12.345" -> "12.34".
 */
export const sanitizeAmountInput = (value: string) => {
  const [whole, ...fraction] = value.replace(/[^\d.]/g, "").split(".");
  if (fraction.length === 0) return whole;
  return `${whole || "0"}.${fraction.join("").slice(0, 2)}`;
};
