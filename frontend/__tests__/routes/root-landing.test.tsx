import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { createRoutesStub, Outlet } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import RootLanding, { clientLoader } from "#/routes/root-landing";
import { clientLoader as settingsIndexLoader } from "#/routes/llm-settings";
import { useAutoAcceptInvitation } from "#/hooks/use-auto-accept-invitation";
import { queryClient } from "#/query-client-config";
import OptionService from "#/api/option-service/option-service.api";
import AuthService from "#/api/auth-service/auth-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { MOCK_TEAM_ORG_ACME } from "#/mocks/org-handlers";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const INVITATION_TOKEN = "inv-test-token";
const INVITATION_LINK = `/?invitation_token=${INVITATION_TOKEN}`;

// Stands in for root-layout, where the app accepts a saved invitation.
function SettingsShell() {
  useAutoAcceptInvitation();
  return <Outlet />;
}

const RouterStub = createRoutesStub([
  {
    Component: SettingsShell,
    children: [
      {
        path: "/",
        Component: RootLanding,
        // @ts-expect-error - loader types won't align between test and app code
        loader: clientLoader,
      },
      {
        path: "/settings",
        loader: settingsIndexLoader,
        Component: () => <div data-testid="llm-settings-screen" />,
      },
      {
        path: "/settings/org-defaults",
        Component: () => <div data-testid="org-default-llm-settings-screen" />,
      },
      {
        path: "/settings/user",
        Component: () => <div data-testid="user-settings-screen" />,
      },
    ],
  },
]);

const renderAt = (path: string) =>
  render(<RouterStub initialEntries={[path]} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });

const signIn = () => {
  vi.spyOn(AuthService, "authenticate").mockResolvedValue(true);
  vi.spyOn(organizationService, "getOrganizations").mockResolvedValue({
    items: [MOCK_TEAM_ORG_ACME],
    currentOrgId: MOCK_TEAM_ORG_ACME.id,
  });
};

const signOut = () => {
  const unauthorized = new AxiosError(
    "Request failed",
    "401",
    undefined,
    undefined,
    {
      status: 401,
      statusText: "",
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: { detail: "Not authenticated" },
    },
  );
  vi.spyOn(AuthService, "authenticate").mockRejectedValue(unauthorized);
  vi.spyOn(organizationService, "getOrganizations").mockRejectedValue(
    unauthorized,
  );
};

describe("Invitation link landing", () => {
  let acceptSpy: ReturnType<typeof vi.spyOn>;

  // The invitation was sent with the token, and the user ends up in the
  // organization they were invited to.
  const expectInvitationAccepted = async () => {
    await waitFor(() =>
      expect(acceptSpy).toHaveBeenCalledExactlyOnceWith({
        token: INVITATION_TOKEN,
      }),
    );
    await waitFor(() =>
      expect(useSelectedOrganizationStore.getState().organizationId).toBe(
        MOCK_TEAM_ORG_ACME.id,
      ),
    );
  };

  beforeEach(() => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    acceptSpy = vi
      .spyOn(organizationService, "acceptInvitation")
      .mockResolvedValue({
        success: true,
        org_id: MOCK_TEAM_ORG_ACME.id,
        org_name: MOCK_TEAM_ORG_ACME.name,
        role: "member",
      });
    vi.spyOn(organizationService, "switchOrganization").mockResolvedValue(
      MOCK_TEAM_ORG_ACME,
    );
  });

  afterEach(() => {
    // Unmount first, so nothing refetches once the cache is cleared.
    cleanup();
    vi.restoreAllMocks();
    queryClient.clear();
    localStorage.clear();
  });

  it("should accept the invitation when a signed-in user opens the link", async () => {
    // Arrange
    signIn();

    // Act
    renderAt(INVITATION_LINK);

    // Assert
    await expectInvitationAccepted();
  });

  it("should accept the invitation after a signed-out user opens the link and signs in", async () => {
    // Arrange: signed out, the link ends on the page sign-in returns to
    signOut();
    const { unmount } = renderAt(INVITATION_LINK);
    await screen.findByTestId("user-settings-screen");
    unmount();
    signIn();
    queryClient.clear();

    // Act: the identity provider sends the user back, now signed in
    renderAt("/settings/user");

    // Assert
    await expectInvitationAccepted();
  });
});
