import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  buildExportFilename,
  formatDateTime,
  formatShortDate,
  rowsToCsv,
} from "#/components/features/admin-dashboard/usage-dashboard-utils";

describe("formatShortDate", () => {
  // Date-only API strings parse as UTC midnight; labels must not shift
  // with the viewer's timezone (previously "2026-07-01" showed "Jun 30"
  // for viewers west of UTC).
  it("renders the UTC calendar date regardless of local timezone", () => {
    expect(formatShortDate("2026-07-01")).toBe("Jul 1");
    expect(formatShortDate("2026-06-23")).toBe("Jun 23");
    expect(formatShortDate("2026-12-31")).toBe("Dec 31");
  });

  it("renders UTC-midnight timestamps on their UTC day", () => {
    expect(formatShortDate("2026-07-01T00:00:00Z")).toBe("Jul 1");
  });
});

describe("formatDateTime", () => {
  const originalTimeZone = process.env.TZ;

  beforeAll(() => {
    process.env.TZ = "America/Los_Angeles";
  });

  afterAll(() => {
    if (originalTimeZone === undefined) {
      delete process.env.TZ;
    } else {
      process.env.TZ = originalTimeZone;
    }
  });

  it("converts timezone-less UTC timestamps to local time", () => {
    expect(formatDateTime("2026-06-16T12:00:00")).toBe("Jun 16, 2026, 5:00 AM");
  });

  it("preserves explicit timezone offsets", () => {
    expect(formatDateTime("2026-06-16T12:00:00+02:00")).toBe(
      "Jun 16, 2026, 3:00 AM",
    );
  });
});

describe("rowsToCsv", () => {
  it("joins headers and rows with commas and newlines", () => {
    const csv = rowsToCsv(
      ["date", "value"],
      [
        ["2026-07-01", 3],
        ["2026-07-02", 5],
      ],
    );
    expect(csv).toBe("date,value\n2026-07-01,3\n2026-07-02,5");
  });

  it("quotes and escapes fields containing commas, quotes, or newlines", () => {
    const csv = rowsToCsv(
      ["model_name", "note"],
      [["gpt-4, turbo", 'has "quotes"\nand newline']],
    );
    expect(csv).toBe(
      'model_name,note\n"gpt-4, turbo","has ""quotes""\nand newline"',
    );
  });

  it("returns just the header row when there are no data rows", () => {
    expect(rowsToCsv(["a", "b"], [])).toBe("a,b");
  });
});

describe("buildExportFilename", () => {
  it("builds a timestamped csv filename with the given prefix", () => {
    const filename = buildExportFilename("model_usage");
    expect(filename).toMatch(/^model_usage_\d{8}_\d{6}\.csv$/);
  });
});
