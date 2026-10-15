import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitProviderConnection } from "#/components/features/settings/git-settings/git-provider-connection";
import UserService from "#/api/user-service/user-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import * as ToastHandlers from "#/utils/custom-toast-handlers";
import OAuthService from "#/api/oauth-service/oauth-service.api";
import { I18nKey } from "#/i18n/declaration";

const defaultFeatureFlags = createMockWebClientConfig().feature_flags;

describe("GitProviderConnection", () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const renderComponent = (isConnected: boolean) =>
    render(
      <QueryClientProvider client={queryClient}>
        <GitProviderConnection
          provider="github"
          providerName="GitHub"
          isConnected={isConnected}
        >
          <span data-testid="connected-only-child">child</span>
        </GitProviderConnection>
      </QueryClientProvider>,
    );

  beforeEach(() => {
    queryClient.clear();
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        app_mode: "saas",
        auth_url: "auth.example.com",
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("should offer to connect a provider that is not connected", () => {
    // Act
    renderComponent(false);

    // Assert
    expect(screen.getByTestId("connect-github-button")).toBeInTheDocument();
    expect(
      screen.queryByTestId("disconnect-github-button"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("connected-only-child"),
    ).not.toBeInTheDocument();
  });

  it("should show the provider extras and a disconnect button once connected", () => {
    // Act
    renderComponent(true);

    // Assert
    expect(screen.getByTestId("connected-only-child")).toBeInTheDocument();
    expect(screen.getByTestId("disconnect-github-button")).toBeInTheDocument();
    expect(
      screen.queryByTestId("connect-github-button"),
    ).not.toBeInTheDocument();
  });

  const oauthV2Config = (
    flag: "enable_integrated_idp" | "enable_oauth_v2_login",
  ) =>
    createMockWebClientConfig({
      app_mode: "saas",
      auth_url: "auth.example.com",
      feature_flags: { ...defaultFeatureFlags, [flag]: true },
    });

  const renderWithConfig = async () => {
    renderComponent(false);
    await waitFor(() =>
      expect(
        queryClient.getQueryData(QUERY_KEYS.WEB_CLIENT_CONFIG),
      ).toBeDefined(),
    );
  };

  it("should send the user to the Keycloak account-linking flow when connecting", async () => {
    // Arrange
    const getLinkFlowSpy = vi.spyOn(OAuthService, "getLinkFlow");
    vi.stubGlobal("location", {
      href: "https://app.example.com/settings/integrations",
    });
    renderComponent(false);

    // Act
    await userEvent.click(screen.getByTestId("connect-github-button"));

    // Assert
    const target = new URL(window.location.href);
    expect(target.pathname).toBe(
      "/realms/allhands/protocol/openid-connect/auth",
    );
    expect(target.searchParams.get("kc_action")).toBe("idp_link:github");
    // Without an OAuth v2 flag every session is a Keycloak one.
    expect(getLinkFlowSpy).not.toHaveBeenCalled();
  });

  it.each(["enable_integrated_idp", "enable_oauth_v2_login"] as const)(
    "should link an OAuth v2 session through the OAuth v2 flow when %s is on",
    async (flag) => {
      // Arrange
      vi.spyOn(OptionService, "getConfig").mockResolvedValue(
        oauthV2Config(flag),
      );
      const getLinkFlowSpy = vi
        .spyOn(OAuthService, "getLinkFlow")
        .mockResolvedValue({ flow: "oauth_v2", provider_id: 3 });
      const startLinkSpy = vi
        .spyOn(OAuthService, "startLink")
        .mockResolvedValue("https://github.com/login/oauth/authorize?state=s");
      vi.stubGlobal("location", {
        href: "https://app.example.com/settings/integrations",
      });
      await renderWithConfig();

      // Act
      await userEvent.click(screen.getByTestId("connect-github-button"));

      // Assert
      await waitFor(() => expect(startLinkSpy).toHaveBeenCalledWith(3));
      expect(getLinkFlowSpy).toHaveBeenCalledWith("github");
      await waitFor(() =>
        expect(window.location.href).toBe(
          "https://github.com/login/oauth/authorize?state=s",
        ),
      );
    },
  );

  it("should link a legacy Keycloak session through Keycloak even when enable_oauth_v2_login is on", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      oauthV2Config("enable_oauth_v2_login"),
    );
    vi.spyOn(OAuthService, "getLinkFlow").mockResolvedValue({
      flow: "keycloak",
      provider_id: null,
    });
    const startLinkSpy = vi.spyOn(OAuthService, "startLink");
    vi.stubGlobal("location", {
      href: "https://app.example.com/settings/integrations",
    });
    await renderWithConfig();

    // Act
    await userEvent.click(screen.getByTestId("connect-github-button"));

    // Assert
    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("kc_action")).toBe(
        "idp_link:github",
      ),
    );
    expect(new URL(window.location.href).host).toBe("auth.example.com");
    expect(startLinkSpy).not.toHaveBeenCalled();
  });

  it("should disable Connect while the link is starting", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      oauthV2Config("enable_integrated_idp"),
    );
    const getLinkFlowSpy = vi
      .spyOn(OAuthService, "getLinkFlow")
      .mockReturnValue(new Promise(() => {}));
    await renderWithConfig();

    // Act
    await userEvent.click(screen.getByTestId("connect-github-button"));

    // Assert
    await waitFor(() => expect(getLinkFlowSpy).toHaveBeenCalledOnce());
    expect(screen.getByTestId("connect-github-button")).toBeDisabled();
  });

  it("should show an error toast when the provider is not configured for OAuth v2", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      oauthV2Config("enable_integrated_idp"),
    );
    vi.spyOn(OAuthService, "getLinkFlow").mockResolvedValue({
      flow: "oauth_v2",
      provider_id: null,
    });
    const startLinkSpy = vi.spyOn(OAuthService, "startLink");
    const errorToastSpy = vi.spyOn(ToastHandlers, "displayErrorToast");
    vi.stubGlobal("location", {
      href: "https://app.example.com/settings/integrations",
    });
    await renderWithConfig();

    // Act
    await userEvent.click(screen.getByTestId("connect-github-button"));

    // Assert
    await waitFor(() =>
      expect(errorToastSpy).toHaveBeenCalledWith(
        I18nKey.SETTINGS$GIT_PROVIDER_NOT_CONFIGURED,
      ),
    );
    expect(startLinkSpy).not.toHaveBeenCalled();
    expect(window.location.href).toBe(
      "https://app.example.com/settings/integrations",
    );
  });

  it("should disconnect the provider after confirmation", async () => {
    // Arrange
    const disconnectSpy = vi
      .spyOn(UserService, "disconnectGitProvider")
      .mockResolvedValue(undefined);
    const successToastSpy = vi.spyOn(ToastHandlers, "displaySuccessToast");
    renderComponent(true);

    // Act
    await userEvent.click(screen.getByTestId("disconnect-github-button"));
    expect(disconnectSpy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("confirm-button"));

    // Assert
    await waitFor(() => expect(disconnectSpy).toHaveBeenCalledWith("github"));
    await waitFor(() => expect(successToastSpy).toHaveBeenCalled());
    expect(screen.queryByTestId("confirmation-modal")).not.toBeInTheDocument();
  });

  it("should show an error toast when disconnecting fails", async () => {
    // Arrange
    vi.spyOn(UserService, "disconnectGitProvider").mockRejectedValue(
      new Error("Failed to disconnect"),
    );
    const errorToastSpy = vi.spyOn(ToastHandlers, "displayErrorToast");
    renderComponent(true);

    // Act
    await userEvent.click(screen.getByTestId("disconnect-github-button"));
    await userEvent.click(screen.getByTestId("confirm-button"));

    // Assert
    await waitFor(() => expect(errorToastSpy).toHaveBeenCalled());
  });
});
