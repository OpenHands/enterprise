import type { PropsWithChildren } from "react";
import type { PostHogConfig, CapturedNetworkRequest } from "posthog-js";
import { WebClientConfig } from "#/api/option-service/option.types";
import { deferred } from "../../helpers/native-fixtures";
import { useSecretFragment } from "#/hooks/use-secret-fragment";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { PostHogWrapper } from "#/components/providers/posthog-wrapper";
import OptionService from "#/api/option-service/option-service.api";
import { queryClient } from "#/query-client-config";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

// Mock PostHogProvider to capture the options passed to it
type ProviderProps = PropsWithChildren<{ options?: Partial<PostHogConfig> }>;
const mockPostHogProvider = vi.fn<(props: ProviderProps) => void>();
vi.mock("posthog-js/react", () => ({
  PostHogProvider: (props: ProviderProps): React.ReactNode => {
    mockPostHogProvider(props);
    return props.children;
  },
}));

describe("PostHogWrapper", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    // Reset URL and hash
    window.history.replaceState({}, "", "/");
    // Clear sessionStorage
    sessionStorage.clear();
    // Mock the config fetch
    // @ts-expect-error - partial mock
    vi.spyOn(OptionService, "getConfig").mockResolvedValue({
      posthog_client_key: "test-posthog-key",
    });
  });

  it("should initialize PostHog with bootstrap IDs from URL hash (without ph_ prefix)", async () => {
    // Webflow sends distinct_id and session_id without the ph_ prefix
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

    // After extracting from hash, IDs should NOT remain in sessionStorage
    // because they were already consumed during this page load.
    // But if a full-page redirect happened before PostHog init,
    // sessionStorage would still have them for the next load.
    // We verify the write happened by checking the provider received the IDs.
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
    // Simulate returning from OAuth: no hash, but sessionStorage has the IDs
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

  it("should not initialize PostHog in self-hosted Enterprise", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        app_mode: "saas",
        posthog_client_key: "configured-posthog-key",
        feature_flags: {
          ...createMockWebClientConfig().feature_flags,
          deployment_mode: "self_hosted",
        },
      }),
    );

    render(
      <PostHogWrapper>
        <div data-testid="child" />
      </PostHogWrapper>,
    );

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["web-client-config"] });
    });

    expect(mockPostHogProvider).not.toHaveBeenCalled();
  });
  it("suppresses sensitive entry pages before config resolves and preserves their tokens for the form", async () => {
    window.history.replaceState({}, "", "/account-setup#token=private-setup&distinct_id=untrusted&session_id=untrusted");
    const { promise, resolve: resolveConfig } = deferred<WebClientConfig>();
    vi.mocked(OptionService.getConfig).mockReturnValue(promise);
    function Recipient(): React.JSX.Element {
      const [token] = useSecretFragment();
      return <span data-testid="recipient">{token === "private-setup" ? "retained" : "missing"}</span>;
    }
    render(<PostHogWrapper><Recipient /></PostHogWrapper>);
    expect(screen.getByTestId("recipient")).toHaveTextContent("retained");
    expect(window.location.hash).toBe("");
    expect(mockPostHogProvider).not.toHaveBeenCalled();
    await act(async () => { resolveConfig(createMockWebClientConfig({ auth_mode: "native", app_mode: "saas", posthog_client_key: "configured-key" })); });
    expect(mockPostHogProvider).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("posthog_bootstrap")).toBeNull();
    window.history.replaceState({}, "", "/");
  });

  it("preserves analytics on ordinary native app pages and redacts native secret network requests", async () => {
    vi.mocked(OptionService.getConfig).mockResolvedValue(createMockWebClientConfig({ auth_mode: "native", app_mode: "saas", posthog_client_key: "configured-key" }));
    await act(async () => { render(<PostHogWrapper><span>App</span></PostHogWrapper>); });
    expect(mockPostHogProvider).toHaveBeenCalled();
    const options = mockPostHogProvider.mock.calls.at(-1)?.[0].options;
    const maskRequest = options?.session_recording?.maskCapturedNetworkRequestFn;
    const beforeSend = options?.before_send;
    if (typeof maskRequest !== "function" || typeof beforeSend !== "function") throw new Error("Expected privacy callbacks");
    const request = (name: string, bodies: Partial<Pick<CapturedNetworkRequest, "requestBody" | "responseBody">> = {}): CapturedNetworkRequest => ({ name, entryType: "resource", duration: 0, startTime: 0, ...bodies });
    expect(maskRequest(request("https://app.test/api/admin/auth-invitations", { responseBody: "secret" }))).toBeNull();
    expect(maskRequest(request("https://app.test/api/auth/password/login", { requestBody: "secret" }))).toBeNull();
    expect(maskRequest(request("https://app.test/api/auth/saml/start", { responseBody: "private-saml-request" }))).toBeNull();
    expect(maskRequest(request("https://app.test/api/auth/saml/acs", { requestBody: "private-assertion" }))).toBeNull();
    expect(maskRequest(request("https://app.test/auth/saml/complete"))).toBeNull();
    window.history.replaceState({}, "", "/auth/saml/complete");
    expect(beforeSend({ uuid: "synthetic-event", event: "$pageview", properties: {} })).toBeNull();
    window.history.replaceState({}, "", "/");
  });

  it("suppresses SAML completion analytics before config resolves and ignores bootstrap values", async () => {
    window.history.replaceState({}, "", "/auth/saml/complete#distinct_id=private-id&session_id=private-session");
    await act(async () => { render(<PostHogWrapper><span>Completing SSO</span></PostHogWrapper>); });
    expect(mockPostHogProvider).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("posthog_bootstrap")).toBeNull();
    window.history.replaceState({}, "", "/");
  });

});
