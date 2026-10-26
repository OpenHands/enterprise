import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoutesStub } from "react-router";
import { http, HttpResponse } from "msw";
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
import { server } from "#/mocks/node";
import {
  resetServiceAccountMockData,
  serviceAccountEnablePartialFailureHandler,
  setServiceAccountBudget,
} from "#/mocks/service-account-handlers";
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

const mockDisplayErrorToast = vi.hoisted(() => vi.fn());

vi.mock("#/utils/custom-toast-handlers", async (importOriginal) => ({
  ...(await importOriginal<typeof import("#/utils/custom-toast-handlers")>()),
  displayErrorToast: mockDisplayErrorToast,
}));

// The global mock of `t` drops interpolation values. Keep them, so a test can
// check that a button name includes the account name.
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-i18next")>()),
  useTranslation: () => ({
    t: (key: string, options?: Record<string, string>) =>
      options ? `${key} ${JSON.stringify(options)}` : key,
    i18n: { language: "en", exists: () => false },
  }),
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

const renderPage = (client: QueryClient = queryClient) =>
  render(<RouteStub initialEntries={["/settings/service-accounts"]} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });

/**
 * A client with the app's real `MutationCache`, which toasts every mutation
 * error unless the mutation sets `meta.disableToast`.
 */
const createAppQueryClient = async () => {
  const { queryClient: appQueryClient } = await vi.importActual<
    typeof import("#/query-client-config")
  >("#/query-client-config");
  appQueryClient.setQueryData(["organizations"], {
    items: INITIAL_MOCK_ORGS,
    currentOrgId: MOCK_TEAM_ORG_ACME.id,
  });
  return appQueryClient;
};

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
    mockDisplayErrorToast.mockClear();
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

  it("shows one error toast when the name is taken (409)", async () => {
    const appQueryClient = await createAppQueryClient();
    renderPage(appQueryClient);
    await findRow("nightly-report");

    await userEvent.click(screen.getByTestId("create-service-account-button"));
    await userEvent.type(
      await screen.findByTestId("service-account-name-input"),
      "nightly-report",
    );
    await userEvent.click(screen.getByTestId("service-account-submit-button"));

    await waitFor(() => expect(mockDisplayErrorToast).toHaveBeenCalled());
    expect(mockDisplayErrorToast).toHaveBeenCalledTimes(1);
    expect(mockDisplayErrorToast).toHaveBeenCalledWith(
      "A service account with this name already exists",
    );
    // The modal stays open so the admin can pick another name.
    expect(screen.getByTestId("service-account-name-input")).toBeVisible();
    appQueryClient.clear();
  });

  it("refreshes the list and toasts once when enable half-succeeds (503)", async () => {
    const appQueryClient = await createAppQueryClient();
    renderPage(appQueryClient);
    await userEvent.click(
      within(await findRow("nightly-report")).getByTestId(
        "toggle-service-account",
      ),
    );
    await waitFor(async () =>
      expect(
        within(await findRow("nightly-report")).getByTestId(
          "service-account-disabled-chip",
        ),
      ).toBeInTheDocument(),
    );

    server.use(serviceAccountEnablePartialFailureHandler);
    await userEvent.click(
      within(await findRow("nightly-report")).getByTestId(
        "toggle-service-account",
      ),
    );

    const enableToasts = () =>
      mockDisplayErrorToast.mock.calls.filter(([message]) =>
        String(message).includes("Retry enable"),
      );
    await waitFor(() => expect(enableToasts()).not.toHaveLength(0));
    expect(enableToasts()).toHaveLength(1);
    // The account is enabled on the server, so the row must not stay stale.
    await waitFor(async () =>
      expect(
        within(await findRow("nightly-report")).queryByTestId(
          "service-account-disabled-chip",
        ),
      ).not.toBeInTheDocument(),
    );
    appQueryClient.clear();
  });

  it("submits the name once when Enter is pressed during the request", async () => {
    let posts = 0;
    server.use(
      http.post("/api/organizations/:orgId/service-accounts", async () => {
        posts += 1;
        await new Promise((resolve) => {
          setTimeout(resolve, 100);
        });
        return HttpResponse.json(
          {
            id: "sa-new",
            org_id: MOCK_TEAM_ORG_ACME.id,
            name: "release-bot",
            is_disabled: false,
            role: "service_account",
            monthly_limit: null,
            budget_limit_disabled: false,
          },
          { status: 201 },
        );
      }),
    );
    renderPage();
    await findRow("nightly-report");

    await userEvent.click(screen.getByTestId("create-service-account-button"));
    await userEvent.type(
      await screen.findByTestId("service-account-name-input"),
      "release-bot{Enter}{Enter}",
    );

    await waitFor(() =>
      expect(
        screen.queryByTestId("service-account-name-input"),
      ).not.toBeInTheDocument(),
    );
    expect(posts).toBe(1);
  });

  it("deletes a secret once when the button is clicked twice", async () => {
    let deletes = 0;
    server.use(
      http.delete(
        "/api/organizations/:orgId/service-accounts/:saId/secrets/:name",
        async () => {
          deletes += 1;
          await new Promise((resolve) => {
            setTimeout(resolve, 100);
          });
          return HttpResponse.json({ message: "Secret deleted successfully" });
        },
      ),
    );
    renderPage();
    await userEvent.click(
      within(await findRow("nightly-report")).getByTestId(
        "manage-service-account",
      ),
    );
    await userEvent.click(
      await screen.findByTestId("add-service-account-secret-button"),
    );
    await userEvent.type(
      await screen.findByTestId("service-account-secret-name-input"),
      "API_TOKEN",
    );
    await userEvent.type(
      screen.getByTestId("service-account-secret-value-input"),
      "v",
    );
    await userEvent.click(
      screen.getByTestId("service-account-secret-submit-button"),
    );
    const deleteButton = await within(
      await screen.findByTestId("service-account-secret-item"),
    ).findByTestId("delete-service-account-secret");

    await userEvent.dblClick(deleteButton);

    // The request is done once the button is enabled again.
    await waitFor(() => expect(deleteButton).toBeEnabled());
    expect(deletes).toBe(1);
  });

  it("names the account in the row buttons and marks the open panel", async () => {
    renderPage();
    await findRow("nightly-report");

    const buttonFor = (action: string) =>
      screen.getByRole("button", {
        name: `SERVICE_ACCOUNTS$ACTION_LABEL ${JSON.stringify({
          action,
          name: "nightly-report",
        })}`,
      });
    const manage = buttonFor("SERVICE_ACCOUNTS$MANAGE");
    expect(manage).toHaveAttribute("aria-expanded", "false");
    expect(buttonFor("BUTTON$RENAME")).toBeInTheDocument();
    expect(buttonFor("SERVICE_ACCOUNTS$DISABLE")).toBeInTheDocument();

    await userEvent.click(manage);
    expect(manage).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(buttonFor("BUTTON$DELETE"));
    expect(
      await screen.findByRole("dialog", {
        name: "SERVICE_ACCOUNTS$DELETE_TITLE",
      }),
    ).toBeInTheDocument();
  });

  it("shows the budget of the service account response without listing the org budgets", async () => {
    setServiceAccountBudget(MOCK_TEAM_ORG_ACME.id, "sa-1", 40);
    const getBudgets = vi.spyOn(organizationService, "getBudgetSettings");
    renderPage();
    await userEvent.click(
      within(await findRow("nightly-report")).getByTestId(
        "manage-service-account",
      ),
    );

    expect(
      await screen.findByTestId("service-account-budget-input"),
    ).toHaveValue(40);
    expect(
      screen.queryByTestId("service-account-budget-unlimited"),
    ).not.toBeInTheDocument();
    expect(getBudgets).not.toHaveBeenCalled();
  });

  it("shows an unlimited budget explicitly and replaces it only on save", async () => {
    setServiceAccountBudget(MOCK_TEAM_ORG_ACME.id, "sa-1", null, true);
    const upsert = vi.spyOn(organizationService, "upsertBudgetOverride");
    renderPage();
    await userEvent.click(
      within(await findRow("nightly-report")).getByTestId(
        "manage-service-account",
      ),
    );

    expect(
      await screen.findByTestId("service-account-budget-unlimited"),
    ).toBeVisible();
    expect(screen.getByTestId("service-account-budget-input")).toHaveValue(
      null,
    );
    expect(upsert).not.toHaveBeenCalled();

    await userEvent.type(
      screen.getByTestId("service-account-budget-input"),
      "30",
    );
    await userEvent.click(screen.getByTestId("service-account-budget-save"));

    await waitFor(() =>
      expect(
        screen.queryByTestId("service-account-budget-unlimited"),
      ).not.toBeInTheDocument(),
    );
    expect(upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        payload: { monthly_limit: 30, is_disabled: false },
      }),
    );
    expect(screen.getByTestId("service-account-budget-input")).toHaveValue(30);
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
