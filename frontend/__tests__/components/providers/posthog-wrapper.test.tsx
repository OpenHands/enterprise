import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { PostHogWrapper } from "#/components/providers/posthog-wrapper";
import OptionService from "#/api/option-service/option-service.api";

// Mock PostHogProvider to capture the options passed to it
const mockPostHogProvider = vi.fn();
vi.mock("posthog-js/react", () => ({
  PostHogProvider: (props: Record<string, unknown>) => {
    mockPostHogProvider(props);
    return props.children;
  },
}));

function encodeHandoff(value: unknown): string {
  const encoded = btoa(
    encodeURIComponent(JSON.stringify(value)).replace(
      /%([0-9A-F]{2})/g,
      (_, hex: string) => String.fromCharCode(Number.parseInt(hex, 16)),
    ),
  );
  return encoded.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

describe("PostHogWrapper", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.location.hash = "";
    localStorage.clear();
    sessionStorage.clear();
    // @ts-expect-error - partial mock
    vi.spyOn(OptionService, "getConfig").mockResolvedValue({
      posthog_client_key: "test-posthog-key",
    });
  });

  it("should initialize PostHog with bootstrap IDs from URL hash (without ph_ prefix)", async () => {
    window.location.hash = "distinct_id=user-123&session_id=session-456";

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(mockPostHogProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          bootstrap: {
            distinctID: "user-123",
            sessionID: "session-456",
          },
        }),
      }),
    );
  });

  it("should clean up URL hash after extracting bootstrap IDs", async () => {
    window.location.hash = "distinct_id=user-123&session_id=session-456";

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(window.location.hash).toBe("");
  });

  it("should persist bootstrap IDs to sessionStorage for OAuth survival", async () => {
    window.location.hash = "distinct_id=user-123&session_id=session-456";

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(mockPostHogProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          bootstrap: {
            distinctID: "user-123",
            sessionID: "session-456",
          },
        }),
      }),
    );
  });

  it("should read bootstrap IDs from sessionStorage when hash is absent (post-OAuth)", async () => {
    sessionStorage.setItem(
      "posthog_bootstrap",
      JSON.stringify({ distinctID: "user-123", sessionID: "session-456" }),
    );

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(mockPostHogProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          bootstrap: {
            distinctID: "user-123",
            sessionID: "session-456",
          },
        }),
      }),
    );
  });

  it("should clean up sessionStorage after consuming bootstrap IDs", async () => {
    sessionStorage.setItem(
      "posthog_bootstrap",
      JSON.stringify({ distinctID: "user-123", sessionID: "session-456" }),
    );

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(sessionStorage.getItem("posthog_bootstrap")).toBeNull();
  });

  it("should initialize PostHog from structured website handoff and register attribution", async () => {
    window.location.hash = `oh_ph_handoff=${encodeHandoff({
      v: 1,
      exp: Date.now() + 60_000,
      nonce: "enterprise-structured",
      distinct_id: "website-anon-id",
      session_id: "website-session-id",
      attribution: {
        utm_source: "newsletter",
        utm_medium: "email",
        utm_campaign: "launch",
        landing_page_category: "home",
        cta_id: "hero-cloud",
        cta_surface: "homepage_hero",
        referring_domain_category: "search",
        full_url: "https://www.openhands.dev/?secret=value",
      },
    })}`;

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    const props = mockPostHogProvider.mock.calls[0][0];
    expect(props).toEqual(
      expect.objectContaining({
        options: expect.objectContaining({
          bootstrap: {
            distinctID: "website-anon-id",
            sessionID: "website-session-id",
          },
        }),
      }),
    );

    const register = vi.fn();
    props.options.loaded({ register });
    expect(register).toHaveBeenCalledWith({
      utm_source: "newsletter",
      utm_medium: "email",
      utm_campaign: "launch",
      landing_page_category: "home",
      cta_id: "hero-cloud",
      cta_surface: "homepage_hero",
      referring_domain_category: "search",
    });
    expect(window.location.hash).toBe("");
  });

  it("should initialize PostHog with health monitoring config (web vitals, error tracking, network timing)", async () => {
    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(mockPostHogProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          capture_performance: {
            network_timing: true,
            web_vitals: true,
          },
          capture_exceptions: true,
        }),
      }),
    );
  });

  it("should initialize without bootstrap when neither hash nor sessionStorage has IDs", async () => {
    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await screen.findByTestId("child");

    expect(mockPostHogProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        options: expect.objectContaining({
          bootstrap: undefined,
        }),
      }),
    );
  });
});
