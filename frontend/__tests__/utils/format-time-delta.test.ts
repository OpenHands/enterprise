import { describe, expect, it, vi, beforeEach } from "vitest";
import { formatTimeDelta, parseDateAsUTC } from "#/utils/format-time-delta";

describe("formatTimeDelta", () => {
  beforeEach(() => {
    const now = new Date("2024-01-01T00:00:00Z");
    vi.useFakeTimers({ now });
  });

  it("formats the yearly time correctly", () => {
    const oneYearAgo = new Date("2023-01-01T00:00:00Z");
    expect(formatTimeDelta(oneYearAgo)).toBe("1y");

    const twoYearsAgo = new Date("2022-01-01T00:00:00Z");
    expect(formatTimeDelta(twoYearsAgo)).toBe("2y");

    const threeYearsAgo = new Date("2021-01-01T00:00:00Z");
    expect(formatTimeDelta(threeYearsAgo)).toBe("3y");
  });

  it("formats the monthly time correctly", () => {
    const oneMonthAgo = new Date("2023-12-01T00:00:00Z");
    expect(formatTimeDelta(oneMonthAgo)).toBe("1mo");

    const twoMonthsAgo = new Date("2023-11-01T00:00:00Z");
    expect(formatTimeDelta(twoMonthsAgo)).toBe("2mo");

    const threeMonthsAgo = new Date("2023-10-01T00:00:00Z");
    expect(formatTimeDelta(threeMonthsAgo)).toBe("3mo");
  });

  it("formats the daily time correctly", () => {
    const oneDayAgo = new Date("2023-12-31T00:00:00Z");
    expect(formatTimeDelta(oneDayAgo)).toBe("1d");

    const twoDaysAgo = new Date("2023-12-30T00:00:00Z");
    expect(formatTimeDelta(twoDaysAgo)).toBe("2d");

    const threeDaysAgo = new Date("2023-12-29T00:00:00Z");
    expect(formatTimeDelta(threeDaysAgo)).toBe("3d");
  });

  it("formats the hourly time correctly", () => {
    const oneHourAgo = new Date("2023-12-31T23:00:00Z");
    expect(formatTimeDelta(oneHourAgo)).toBe("1h");

    const twoHoursAgo = new Date("2023-12-31T22:00:00Z");
    expect(formatTimeDelta(twoHoursAgo)).toBe("2h");

    const threeHoursAgo = new Date("2023-12-31T21:00:00Z");
    expect(formatTimeDelta(threeHoursAgo)).toBe("3h");
  });

  it("formats the minute time correctly", () => {
    const oneMinuteAgo = new Date("2023-12-31T23:59:00Z");
    expect(formatTimeDelta(oneMinuteAgo)).toBe("1m");

    const twoMinutesAgo = new Date("2023-12-31T23:58:00Z");
    expect(formatTimeDelta(twoMinutesAgo)).toBe("2m");

    const threeMinutesAgo = new Date("2023-12-31T23:57:00Z");
    expect(formatTimeDelta(threeMinutesAgo)).toBe("3m");
  });

  it("formats the second time correctly", () => {
    const oneSecondAgo = new Date("2023-12-31T23:59:59Z");
    expect(formatTimeDelta(oneSecondAgo)).toBe("1s");

    const twoSecondsAgo = new Date("2023-12-31T23:59:58Z");
    expect(formatTimeDelta(twoSecondsAgo)).toBe("2s");

    const threeSecondsAgo = new Date("2023-12-31T23:59:57Z");
    expect(formatTimeDelta(threeSecondsAgo)).toBe("3s");
  });

  it("parses timezone-naive strings as UTC and offset strings as given", () => {
    // Naive strings come from the backend's UTC-labelled storage.
    expect(formatTimeDelta("2023-12-31T23:59:00")).toBe("1m");
    expect(formatTimeDelta("2023-12-31T23:58:59.500000")).toBe("1m");
    // Explicit offsets are honoured regardless of the browser zone.
    expect(formatTimeDelta("2024-01-01T00:59:00+01:00")).toBe("1m");
    expect(formatTimeDelta("2023-12-31T18:59:00-05:00")).toBe("1m");
    expect(parseDateAsUTC("2023-12-31T23:59:00").toISOString()).toBe(
      "2023-12-31T23:59:00.000Z",
    );
  });

  it("never renders a negative delta for timestamps ahead of the client clock", () => {
    // Client clock slightly behind the server, or a timestamp mislabelled as UTC.
    expect(formatTimeDelta(new Date("2024-01-01T00:00:03Z"))).toBe("0s");
    expect(formatTimeDelta("2024-01-01T00:15:49Z")).toBe("0s");
    expect(formatTimeDelta("2024-01-01T02:00:00")).toBe("0s");
    expect(formatTimeDelta("2024-01-01T01:00:00+00:00")).toBe("0s");
  });
});
