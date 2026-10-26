import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoutesStub } from "react-router";
import { organizationService } from "#/api/organization-service/organization-service.api";
import OptionService from "#/api/option-service/option-service.api";
import ServiceAccountsScreen, {
  clientLoader as serviceAccountsClientLoader,
} from "#/routes/service-accounts";
import SettingsScreen, {
  clientLoader as settingsClientLoader,
} from "#/routes/settings";
import {
  INITIAL_MOCK_ORGS,
  MOCK_ROLE_PERMISSIONS,
  MOCK_TEAM_ORG_ACME,
  resetOrgMockData,
} from "#/mocks/org-handlers";
import { resetServiceAccountMockData } from "#/mocks/service-account-handlers";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { OrganizationMember } from "#/types/org";

const mockQueryClient = vi.hoisted(() => {
  const { QueryClient } = require("@tanstack/react-query");
  return new QueryClient();
});

vi.mock("#/query-client-config", () => ({
  queryClient: mockQueryClient,
}));

vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: true }),
}));

function ServiceAccountsWithPortalRoot() {
  return (
    <div>
      <ServiceAccountsScreen />
      <div data-testid="portal-root" id="portal-root" />
    </div>
  );
}

const RouteStub = createRoutesStub([
  {
    // @ts-expect-error - type mismatch
    loader: settingsClientLoader,
    Component: SettingsScreen,
    path: "/settings",
    HydrateFallback: () => <div>Loading...</div>,
    children: [
      {
        loader: serviceAccountsClientLoader,
        Component: ServiceAccountsWithPortalRoot,
        path: "/settings/service-accounts",
        handle: { hideTitle: true },
      },
      {
        Component: () => <div data-testid="user-settings-screen" />,
        path: "/settings/user",
      },
    ],
  },
]);

const userWithRole = (role: "owner" | "member"): OrganizationMember => ({
  org_id: MOCK_TEAM_ORG_ACME.id,
  user_id: "1",
  email: "test@example.com",
  role,
  permissions: MOCK_ROLE_PERMISSIONS[role],
  llm_api_key: "**********",
  max_iterations: 20,
  llm_model: "gpt-4",
  llm_base_url: "https://api.openai.com",
  status: "active",
});

let queryClient: QueryClient;

const renderPage = () =>
  render(<RouteStub initialEntries={["/settings/service-accounts"]} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });

const findRow = async (name: string) => {
  const rows = await screen.findAllByTestId("service-account-item");
  const row = rows.find((item) => within(item).queryByText(name));
  if (!row) throw new Error(`No service account row named ${name}`);
  return row;
};

describe("Service accounts route", () => {
  beforeEach(() => {
    useSelectedOrganizationStore.setState({
      organizationId: MOCK_TEAM_ORG_ACME.id,
    });
    mockQueryClient.setQueryData(["organizations"], {
      items: [MOCK_TEAM_ORG_ACME],
      currentOrgId: MOCK_TEAM_ORG_ACME.id,
    });
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    queryClient.setQueryData(["organizations"], {
      items: INITIAL_MOCK_ORGS,
      currentOrgId: MOCK_TEAM_ORG_ACME.id,
    });
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    vi.spyOn(organizationService, "getMe").mockResolvedValue(
      userWithRole("owner"),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetOrgMockData();
    resetServiceAccountMockData();
    useSelectedOrganizationStore.setState({ organizationId: null });
    mockQueryClient.clear();
    queryClient.clear();
  });

  it("lists the service accounts of the org", async () => {
    renderPage();

    const row = await findRow("nightly-report");
    expect(row).toBeInTheDocument();
  });

  it("creates a service account and refreshes the list", async () => {
    renderPage();
    await findRow("nightly-report");

    await userEvent.click(screen.getByTestId("create-service-account-button"));
    await userEvent.type(
      await screen.findByTestId("service-account-name-input"),
      "release-bot",
    );
    await userEvent.click(screen.getByTestId("service-account-submit-button"));

    expect(await findRow("release-bot")).toBeInTheDocument();
    expect(await findRow("nightly-report")).toBeInTheDocument();
  });

  it("renames a service account", async () => {
    renderPage();
    const row = await findRow("nightly-report");

    await userEvent.click(within(row).getByTestId("rename-service-account"));
    const input = await screen.findByTestId("service-account-name-input");
    await userEvent.clear(input);
    await userEvent.type(input, "weekly-report");
    await userEvent.click(screen.getByTestId("service-account-submit-button"));

    expect(await findRow("weekly-report")).toBeInTheDocument();
    expect(screen.queryByText("nightly-report")).not.toBeInTheDocument();
  });

  it("disables and enables a service account", async () => {
    renderPage();
    const row = await findRow("nightly-report");
    expect(
      within(row).queryByTestId("service-account-disabled-chip"),
    ).not.toBeInTheDocument();

    await userEvent.click(within(row).getByTestId("toggle-service-account"));

    await waitFor(async () => {
      const updated = await findRow("nightly-report");
      expect(
        within(updated).getByTestId("service-account-disabled-chip"),
      ).toBeInTheDocument();
    });

    const disabledRow = await findRow("nightly-report");
    await userEvent.click(
      within(disabledRow).getByTestId("toggle-service-account"),
    );

    await waitFor(async () => {
      const updated = await findRow("nightly-report");
      expect(
        within(updated).queryByTestId("service-account-disabled-chip"),
      ).not.toBeInTheDocument();
    });
  });

  it("deletes a service account only after confirmation", async () => {
    renderPage();
    const row = await findRow("nightly-report");

    await userEvent.click(within(row).getByTestId("delete-service-account"));
    await userEvent.click(await screen.findByTestId("cancel-button"));
    expect(await findRow("nightly-report")).toBeInTheDocument();

    await userEvent.click(
      within(await findRow("nightly-report")).getByTestId(
        "delete-service-account",
      ),
    );
    await userEvent.click(await screen.findByTestId("confirm-button"));

    await waitFor(() => {
      expect(screen.queryByText("nightly-report")).not.toBeInTheDocument();
    });
  });

  it("adds, edits and deletes a secret without ever showing its value", async () => {
    renderPage();
    const row = await findRow("nightly-report");
    await userEvent.click(within(row).getByTestId("manage-service-account"));

    await userEvent.click(
      await screen.findByTestId("add-service-account-secret-button"),
    );
    await userEvent.type(
      await screen.findByTestId("service-account-secret-name-input"),
      "API_TOKEN",
    );
    await userEvent.type(
      screen.getByTestId("service-account-secret-value-input"),
      "super-secret-value",
    );
    await userEvent.click(
      screen.getByTestId("service-account-secret-submit-button"),
    );

    const secretRow = await screen.findByTestId("service-account-secret-item");
    expect(within(secretRow).getByText("API_TOKEN")).toBeInTheDocument();
    expect(screen.queryByText("super-secret-value")).not.toBeInTheDocument();

    // Edit replaces the value; the name stays fixed.
    await userEvent.click(
      within(secretRow).getByTestId("edit-service-account-secret"),
    );
    expect(
      await screen.findByTestId("service-account-secret-name-input"),
    ).toBeDisabled();
    expect(
      screen.getByTestId("service-account-secret-value-input"),
    ).toHaveValue("");
    await userEvent.type(
      screen.getByTestId("service-account-secret-value-input"),
      "new-value",
    );
    await userEvent.click(
      screen.getByTestId("service-account-secret-submit-button"),
    );
    expect(
      await screen.findByTestId("service-account-secret-item"),
    ).toBeVisible();
    expect(screen.queryByText("new-value")).not.toBeInTheDocument();

    await userEvent.click(
      within(screen.getByTestId("service-account-secret-item")).getByTestId(
        "delete-service-account-secret",
      ),
    );
    await waitFor(() => {
      expect(
        screen.queryByTestId("service-account-secret-item"),
      ).not.toBeInTheDocument();
    });
  });

  it("sets the monthly budget of a service account", async () => {
    renderPage();
    const row = await findRow("nightly-report");
    await userEvent.click(within(row).getByTestId("manage-service-account"));

    const input = await screen.findByTestId("service-account-budget-input");
    await userEvent.type(input, "75");
    await userEvent.click(screen.getByTestId("service-account-budget-save"));

    await waitFor(() => {
      expect(screen.getByTestId("service-account-budget-input")).toHaveValue(
        75,
      );
    });
    expect(
      screen.getByTestId("service-account-budget-remove"),
    ).toBeInTheDocument();
  });

  it("redirects a member away from the page", async () => {
    vi.spyOn(organizationService, "getMe").mockResolvedValue(
      userWithRole("member"),
    );
    renderPage();

    expect(
      await screen.findByTestId("user-settings-screen"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("service-accounts-settings"),
    ).not.toBeInTheDocument();
  });
});
