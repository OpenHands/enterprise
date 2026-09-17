import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitProviderConnection } from "#/components/features/settings/git-settings/git-provider-connection";
import UserService from "#/api/user-service/user-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import * as ToastHandlers from "#/utils/custom-toast-handlers";

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
    expect(screen.getByTestId("github-status-text")).toHaveTextContent(
      "STATUS$NOT_CONNECTED",
    );
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
    expect(screen.getByTestId("github-status-text")).toHaveTextContent(
      "STATUS$CONNECTED",
    );
    expect(screen.getByTestId("connected-only-child")).toBeInTheDocument();
    expect(screen.getByTestId("disconnect-github-button")).toBeInTheDocument();
    expect(
      screen.queryByTestId("connect-github-button"),
    ).not.toBeInTheDocument();
  });

  it("should send the user to the Keycloak account-linking flow when connecting", async () => {
    // Arrange
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
