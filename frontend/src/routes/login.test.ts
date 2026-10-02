import { describe, expect, it } from "vitest";
import { getSafeReturnTo } from "./login";

describe("getSafeReturnTo", () => {
  it("prefers returnTo when present", () => {
    const params = new URLSearchParams({
      returnTo: "/settings",
      redirect: "/canvas/automations",
    });

    expect(getSafeReturnTo(params)).toBe("/settings");
  });

  it("accepts legacy redirect for automation login links", () => {
    const params = new URLSearchParams({
      redirect: "/canvas/automations?tab=runs",
    });

    expect(getSafeReturnTo(params)).toBe("/canvas/automations?tab=runs");
  });

  it("rejects absolute redirect targets", () => {
    expect(
      getSafeReturnTo(new URLSearchParams({ redirect: "https://example.com" })),
    ).toBe("/");
    expect(
      getSafeReturnTo(new URLSearchParams({ redirect: "//example.com" })),
    ).toBe("/");
  });

  it("rejects encoded and backslash redirect targets", () => {
    expect(
      getSafeReturnTo(new URLSearchParams("returnTo=%2F%2Fevil.example")),
    ).toBe("/");
    expect(
      getSafeReturnTo(new URLSearchParams({ returnTo: "/\\evil.example" })),
    ).toBe("/");
  });
});
