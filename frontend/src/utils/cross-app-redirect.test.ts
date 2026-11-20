import { afterEach, describe, expect, it, vi } from "vitest";
import { isCrossAppPath, navigateOrHardRedirect } from "./cross-app-redirect";

afterEach(() => vi.unstubAllGlobals());

describe("isCrossAppPath", () => {
  it("detects Automations app routes under Canvas", () => {
    expect(isCrossAppPath("/canvas/automations")).toBe(true);
    expect(isCrossAppPath("/canvas/automations?login_method=github")).toBe(
      true,
    );
    expect(isCrossAppPath("/canvas/automations/abc?tab=runs")).toBe(true);
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

  it("loads MCP consent from the backend with its request intact", () => {
    const replace = vi.fn();
    const navigate = vi.fn();
    vi.stubGlobal("location", { origin: "https://example.com", replace });
    const destination =
      "/external-mcp/consent?request=opaque_handle&login_method=github";

    navigateOrHardRedirect(navigate, destination, { replace: true });

    expect(replace).toHaveBeenCalledWith(destination);
    expect(navigate).not.toHaveBeenCalled();
    expect(isCrossAppPath("/external-mcp-old/consent")).toBe(false);
    expect(isCrossAppPath("//evil.com/external-mcp/consent")).toBe(false);
  });

  it("does not match similarly-prefixed main app routes", () => {
    expect(isCrossAppPath("/automations")).toBe(false);
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
