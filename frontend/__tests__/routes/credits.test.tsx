import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { selectOrganization } from "test-utils";
import CreditsSettingsScreen from "#/routes/credits";
import { organizationService } from "#/api/organization-service/organization-service.api";
import SettingsScreen, { clientLoader } from "#/routes/settings";
import {
  resetOrgMockData,
  MOCK_TEAM_ORG_ACME,
  INITIAL_MOCK_ORGS,
} from "#/mocks/org-handlers";
import OptionService from "#/api/option-service/option-service.api";
import BillingService from "#/api/billing-service/billing-service.api";
import { OrganizationMember } from "#/types/org";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const mockQueryClient = vi.hoisted(() => {
  const { QueryClient } = require("@tanstack/react-query");
  return new QueryClient();
});

vi.mock("#/query-client-config", () => ({
  queryClient: mockQueryClient,
}));

function CreditsWithPortalRoot() {
  return (
    <div>
      <CreditsSettingsScreen />
      <div data-testid="portal-root" id="portal-root" />
    </div>
  );
}

const RouteStub = createRoutesStub([
  {
    Component: () => <div data-testid="home-screen" />,
    path: "/",
  },
  {
    // @ts-expect-error - type mismatch
    loader: clientLoader,
    Component: SettingsScreen,
    path: "/settings",
    HydrateFallback: () => <div>Loading...</div>,
    children: [
      {
        Component: CreditsWithPortalRoot,
        path: "/settings/credits",
      },
      {
        Component: () => <div data-testid="user-settings-screen" />,
        path: "/settings/user",
      },
    ],
  },
]);

let queryClient: QueryClient;

const renderCredits = () =>
  render(<RouteStub initialEntries={["/settings/credits"]} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });

vi.mock("react-i18next", async () => {
  const actual =
    await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string) => {
        const translations: Record<string, string> = {
          ORG$CREDITS: "Credits",
          ORG$ADD: "+ Add",
          ORG$ADD_CREDITS: "Add Credits",
          ORG$NEXT: "Next",
          ORG$SELECT_ORGANIZATION_PLACEHOLDER: "Please select an organization",
          ORG$PERSONAL_WORKSPACE: "Personal Workspace",
          PAYMENT$SUCCESS: "Payment successful",
          PAYMENT$CANCELLED: "Payment cancelled",
        };
        return translations[key] || key;
      },
      i18n: {
        changeLanguage: vi.fn(),
      },
    }),
  };
});

vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: true }),
}));

describe("Credits Route", () => {
  const getMeSpy = vi.spyOn(organizationService, "getMe");

  const TEST_USERS: Record<"OWNER" | "ADMIN", OrganizationMember> = {
    OWNER: {
      org_id: "1",
      user_id: "1",
      email: "test@example.com",
      role: "owner",
      llm_api_key: "**********",
      max_iterations: 20,
      llm_model: "gpt-4",
      llm_base_url: "https://api.openai.com",
      status: "active",
    },
    ADMIN: {
      org_id: "1",
      user_id: "1",
      email: "test@example.com",
      role: "admin",
      llm_api_key: "**********",
      max_iterations: 20,
      llm_model: "gpt-4",
      llm_base_url: "https://api.openai.com",
      status: "active",
    },
  };

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

    resetOrgMockData();
    getMeSpy.mockResolvedValue(TEST_USERS.OWNER);
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        app_mode: "saas",
        feature_flags: {
          enable_billing: true,
          hide_llm_settings: false,
          enable_jira: false,
          enable_jira_dc: false,
          enable_linear: false,
          hide_users_page: false,
          hide_billing_page: false,
          hide_integrations_page: false,
          enable_onboarding: false,
        },
      }),
    );
  });

  afterEach(() => {
    vi.clearAllMocks();
    resetOrgMockData();
    useSelectedOrganizationStore.setState({ organizationId: null });
    mockQueryClient.clear();
    queryClient?.clear();
  });

  it("should render the available credits", async () => {
    renderCredits();
    await screen.findByTestId("org-credits");

    await waitFor(() => {
      const credits = screen.getByTestId("available-credits");
      expect(credits).toHaveTextContent("100");
    });
  });

  it("should be able to add credits", async () => {
    const createCheckoutSessionSpy = vi.spyOn(
      BillingService,
      "createCheckoutSession",
    );

    renderCredits();
    await screen.findByTestId("org-credits");

    expect(screen.queryByTestId("add-credits-form")).not.toBeInTheDocument();
    const addCreditsButton = await waitFor(() => screen.getByText(/\+ Add/i));
    await userEvent.click(addCreditsButton);

    const addCreditsForm = screen.getByTestId("add-credits-form");
    expect(addCreditsForm).toBeInTheDocument();

    const amountInput = within(addCreditsForm).getByTestId("amount-input");
    const nextButton = within(addCreditsForm).getByRole("button", {
      name: /next/i,
    });

    await userEvent.type(amountInput, "1000");
    await userEvent.click(nextButton);

    await waitFor(() =>
      expect(createCheckoutSessionSpy).toHaveBeenCalledWith(1000),
    );

    await waitFor(() =>
      expect(screen.queryByTestId("add-credits-form")).not.toBeInTheDocument(),
    );
  });

  it("should close the modal when clicking cancel", async () => {
    const createCheckoutSessionSpy = vi.spyOn(
      BillingService,
      "createCheckoutSession",
    );
    renderCredits();
    await screen.findByTestId("org-credits");

    const addCreditsButton = await waitFor(() => screen.getByText(/\+ Add/i));
    await userEvent.click(addCreditsButton);

    const addCreditsForm = screen.getByTestId("add-credits-form");
    const cancelButton = within(addCreditsForm).getByRole("button", {
      name: /close/i,
    });

    await userEvent.click(cancelButton);

    expect(screen.queryByTestId("add-credits-form")).not.toBeInTheDocument();
    expect(createCheckoutSessionSpy).not.toHaveBeenCalled();
  });

  it("should show add credits option for ADMIN role", async () => {
    getMeSpy.mockResolvedValue(TEST_USERS.ADMIN);
    renderCredits();
    await selectOrganization({ orgIndex: 3 });

    await waitFor(() => {
      expect(screen.getByTestId("available-credits")).toBeInTheDocument();
    });

    expect(screen.getByText(/\+ Add/i)).toBeInTheDocument();
  });

  it("should hide credits UI when enable_billing is false", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        app_mode: "saas",
        feature_flags: {
          enable_billing: false,
          hide_llm_settings: false,
          enable_jira: false,
          enable_jira_dc: false,
          enable_linear: false,
          hide_users_page: false,
          hide_billing_page: false,
          hide_integrations_page: false,
          enable_onboarding: false,
        },
      }),
    );

    renderCredits();

    await waitFor(() => {
      expect(screen.queryByTestId("available-credits")).not.toBeInTheDocument();
      expect(screen.queryByText(/\+ Add/i)).not.toBeInTheDocument();
    });
  });
});
