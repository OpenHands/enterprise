import { describe, expect, it } from "vitest";
import {
  isCrossAppPath,
  normalizeLegacyAutomationsPath,
} from "./cross-app-redirect";

describe("isCrossAppPath", () => {
  it("detects automation app routes", () => {
    expect(isCrossAppPath("/automations")).toBe(true);
    expect(isCrossAppPath("/automations?login_method=github")).toBe(true);
    expect(isCrossAppPath("/automations/abc?tab=runs")).toBe(true);
  });

  it("detects Canvas app routes", () => {
    expect(isCrossAppPath("/canvas")).toBe(true);
    expect(isCrossAppPath("/canvas?login_method=github")).toBe(true);
    expect(isCrossAppPath("/canvas/conversations/abc")).toBe(true);
  });

  it("detects Integrations Hub app routes", () => {
    expect(isCrossAppPath("/integrations-hub")).toBe(true);
    expect(isCrossAppPath("/integrations-hub/integrations")).toBe(true);
    expect(
      isCrossAppPath("/integrations-hub/integrations?login_method=github"),
    ).toBe(true);
  });

  it("does not match similarly-prefixed main app routes", () => {
    expect(isCrossAppPath("/automation")).toBe(false);
    expect(isCrossAppPath("/automations-old")).toBe(false);
    expect(isCrossAppPath("/canvases")).toBe(false);
    expect(isCrossAppPath("/integrations")).toBe(false);
    expect(isCrossAppPath("/integrations-hub-old")).toBe(false);
    expect(isCrossAppPath("/settings?returnTo=/automations")).toBe(false);
    expect(isCrossAppPath("/settings?returnTo=/canvas")).toBe(false);
    expect(isCrossAppPath("/settings?returnTo=/integrations-hub")).toBe(false);
  });

  it("does not treat off-origin targets as cross-app paths", () => {
    expect(isCrossAppPath("//evil.com/automations")).toBe(false);
    expect(isCrossAppPath("https://evil.com/automations")).toBe(false);
    expect(isCrossAppPath("//evil.com/canvas")).toBe(false);
    expect(isCrossAppPath("https://evil.com/canvas")).toBe(false);
  });
});

describe("normalizeLegacyAutomationsPath", () => {
  it("rewrites a bare /automations path", () => {
    expect(normalizeLegacyAutomationsPath("/automations")).toBe(
      "/canvas/automations",
    );
  });

  it("rewrites an automations deep link", () => {
    expect(normalizeLegacyAutomationsPath("/automations/abc")).toBe(
      "/canvas/automations/abc",
    );
  });

  it("preserves a query string", () => {
    expect(normalizeLegacyAutomationsPath("/automations?tab=runs")).toBe(
      "/canvas/automations?tab=runs",
    );
  });

  it("preserves a query string on a deep link", () => {
    expect(normalizeLegacyAutomationsPath("/automations/abc?tab=runs")).toBe(
      "/canvas/automations/abc?tab=runs",
    );
  });

  it("leaves the canonical /canvas/automations path unchanged", () => {
    expect(normalizeLegacyAutomationsPath("/canvas/automations")).toBe(
      "/canvas/automations",
    );
  });

  it("leaves non-automations paths unchanged", () => {
    expect(normalizeLegacyAutomationsPath("/settings")).toBe("/settings");
    expect(normalizeLegacyAutomationsPath("/canvas/conversations/abc")).toBe(
      "/canvas/conversations/abc",
    );
  });
});
