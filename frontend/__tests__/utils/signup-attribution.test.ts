import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  claimMarketingVisit,
  clearSignupEntry,
  getSignupEntry,
  rememberCloudEntry,
} from "#/utils/signup-attribution";

describe("Cloud signup entry", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-23T10:00:00Z"));
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.useRealTimers();
    sessionStorage.clear();
  });

  it("retains a website entry and safe campaign through OAuth and terms acceptance", () => {
    rememberCloudEntry("marketing_website", {
      utm_source: "newsletter",
      utm_campaign: "fall",
      device_code: "secret",
    });
    window.history.replaceState(null, "", "/accept-tos");

    expect(getSignupEntry()).toEqual({
      signup_entry_source: "marketing_website",
      utm_source: "newsletter",
      utm_campaign: "fall",
    });
    expect(claimMarketingVisit()).toEqual({
      signup_entry_source: "marketing_website",
      utm_source: "newsletter",
      utm_campaign: "fall",
    });
    expect(claimMarketingVisit()).toBeNull();
    clearSignupEntry();
    expect(getSignupEntry()).toBeNull();
  });

  it("accepts local device verification only on its route and expires the entry", () => {
    window.history.replaceState(
      null,
      "",
      "/oauth/device/verify?user_code=ABC&signup_entry_source=local_canvas",
    );
    rememberCloudEntry();
    expect(getSignupEntry()).toEqual({ signup_entry_source: "local_canvas" });
    expect(claimMarketingVisit()).toBeNull();

    vi.advanceTimersByTime(31 * 60 * 1000);
    expect(getSignupEntry()).toBeNull();
    window.history.replaceState(null, "", "/?signup_entry_source=local_canvas");
    rememberCloudEntry();
    expect(getSignupEntry()).toBeNull();
  });

  it("remembers website and docs origins without an analytics identity handoff", () => {
    window.history.replaceState(null, "", "/?signup_entry_source=docs");
    rememberCloudEntry();
    expect(getSignupEntry()).toEqual({ signup_entry_source: "docs" });
    window.history.replaceState(
      null,
      "",
      "/?signup_entry_source=marketing_website",
    );
    rememberCloudEntry();
    expect(getSignupEntry()).toEqual({
      signup_entry_source: "marketing_website",
    });
  });

  it("reads a same-origin Canvas entry nested in the login returnTo across OAuth", () => {
    const returnTo =
      "/canvas?signup_entry_source=marketing_website&utm_source=newsletter&code=secret";
    window.history.replaceState(
      null,
      "",
      `/login?returnTo=${encodeURIComponent(returnTo)}`,
    );
    rememberCloudEntry();
    window.history.replaceState(null, "", "/accept-tos");

    expect(getSignupEntry()).toEqual({
      signup_entry_source: "marketing_website",
      utm_source: "newsletter",
    });
  });

  it("ignores external returnTo attribution and prefers the top-level entry", () => {
    window.history.replaceState(
      null,
      "",
      `/login?returnTo=${encodeURIComponent("https://other.example/canvas?signup_entry_source=docs")}`,
    );
    rememberCloudEntry();
    expect(getSignupEntry()).toBeNull();

    window.history.replaceState(
      null,
      "",
      `/login?signup_entry_source=docs&returnTo=${encodeURIComponent("/canvas?signup_entry_source=marketing_website")}`,
    );
    rememberCloudEntry();
    expect(getSignupEntry()).toEqual({ signup_entry_source: "docs" });
  });

  it("rejects unknown sources and sensitive campaign parameters", () => {
    rememberCloudEntry("evil", {
      utm_source: "user@example.com",
      utm_campaign: "https://example.com/?code=secret",
    });
    expect(getSignupEntry()).toBeNull();
  });

  it("keeps a marketing visit claimed when the same landing is loaded again", () => {
    window.history.replaceState(
      null,
      "",
      "/settings?signup_entry_source=docs&utm_campaign=launch",
    );
    rememberCloudEntry();
    expect(claimMarketingVisit()).toEqual({
      signup_entry_source: "docs",
      utm_campaign: "launch",
    });

    rememberCloudEntry();
    expect(claimMarketingVisit()).toBeNull();

    window.history.replaceState(
      null,
      "",
      "/settings?signup_entry_source=docs&utm_campaign=next",
    );
    rememberCloudEntry();
    expect(claimMarketingVisit()).toEqual({
      signup_entry_source: "docs",
      utm_campaign: "next",
    });
  });

  it("captures a new campaign for a returning direct Cloud visitor", () => {
    window.history.replaceState(
      null,
      "",
      "/?utm_source=search&utm_campaign=october&code=secret",
    );
    rememberCloudEntry();
    expect(claimMarketingVisit()).toEqual({
      signup_entry_source: "direct_or_unknown",
      utm_source: "search",
      utm_campaign: "october",
    });
    expect(claimMarketingVisit()).toBeNull();
  });
});
