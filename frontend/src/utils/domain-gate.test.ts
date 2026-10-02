import { afterEach, describe, expect, it, vi } from "vitest";
import { isOpenHandsHostedDomain } from "#/utils/domain-gate";

describe("isOpenHandsHostedDomain", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function setHostname(hostname: string) {
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      hostname,
    });
  }

  it.each([
    "app.all-hands.dev",
    "staging.all-hands.dev",
    "pr-123.staging.all-hands.dev",
    "pr-1.staging.all-hands.dev",
  ])("returns true for hosted domain %s", (hostname) => {
    setHostname(hostname);
    expect(isOpenHandsHostedDomain()).toBe(true);
  });

  it.each([
    "localhost",
    "127.0.0.1",
    "enterprise.example.com",
    "openhands.internal.corp",
    "app.all-hands.dev.evil.com",
    "pr-staging.all-hands.dev",
    "pr-.staging.all-hands.dev",
    "pr-abc.staging.all-hands.dev",
  ])("returns false for non-hosted domain %s", (hostname) => {
    setHostname(hostname);
    expect(isOpenHandsHostedDomain()).toBe(false);
  });

  it("is case-insensitive", () => {
    setHostname("APP.ALL-HANDS.DEV");
    expect(isOpenHandsHostedDomain()).toBe(true);
  });
});
