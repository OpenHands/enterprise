import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoutesStub, Link } from "react-router";
import { http, HttpResponse } from "msw";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { scimService } from "#/api/organization-service/scim.api";
import OptionService from "#/api/option-service/option-service.api";
import ScimSettingsScreen from "#/routes/scim-settings";
import SuperAdminLayout from "#/routes/super-admin";
import {
  INITIAL_MOCK_ORGS,
  MOCK_ROLE_PERMISSIONS,
  MOCK_TEAM_ORG_ACME,
  MOCK_TEAM_ORG_BETA,
  resetOrgMockData,
} from "#/mocks/org-handlers";
import { server } from "#/mocks/node";
import {
  resetScimMockData,
  setScimMockCredentials,
} from "#/mocks/scim-handlers";
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
// check that a button name includes the credential name.
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

function ScimWithPortalRoot() {
  return (
    <div>
      <ScimSettingsScreen />
      <Link to={`/super-admin/organizations/${MOCK_TEAM_ORG_BETA.id}/scim`}>
        Open Beta SCIM
      </Link>
      <div data-testid="portal-root" id="portal-root" />
    </div>
  );
}

const RouteStub = createRoutesStub([
  {
    path: "/super-admin",
    Component: SuperAdminLayout,
    children: [
      {
        Component: ScimWithPortalRoot,
        path: "organizations/:orgId/scim",
        handle: { hideTitle: true },
      },
      {
        Component: () => <div data-testid="organizations-screen" />,
        path: "organizations",
      },
    ],
  },
  {
    Component: () => <div data-testid="settings-screen" />,
    path: "/settings",
  },
]);

const userWithRole = (
  role: "owner" | "admin" | "member",
  superAdmin = false,
): OrganizationMember => ({
  org_id: MOCK_TEAM_ORG_ACME.id,
  user_id: "1",
  email: "test@example.com",
  role,
  permissions: [
    ...MOCK_ROLE_PERMISSIONS[role],
    ...(superAdmin ? ["manage_oauth_providers", "manage_super_admins"] : []),
  ],
  llm_api_key: "**********",
  max_iterations: 20,
  llm_model: "gpt-4",
  llm_base_url: "https://api.openai.com",
  status: "active",
});

const SAAS_CONFIG = createMockWebClientConfig({ app_mode: "saas" });
SAAS_CONFIG.feature_flags.enable_scim = true;
SAAS_CONFIG.feature_flags.enable_super_admin = true;

let queryClient: QueryClient;

const renderPage = () =>
  render(
    <RouteStub
      initialEntries={[
        `/super-admin/organizations/${MOCK_TEAM_ORG_ACME.id}/scim`,
      ]}
    />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    },
  );

const findRow = async (name: string) => {
  const rows = await screen.findAllByTestId("scim-credential-item");
  const row = rows.find((item) => within(item).queryByText(name));
  if (!row) throw new Error(`No SCIM credential row named ${name}`);
  return row;
};

const credentialsUrl = `/api/organizations/${MOCK_TEAM_ORG_ACME.id}/scim/credentials`;

const mockClipboard = (writeText: ReturnType<typeof vi.fn>) => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
};

const submitCredential = async (name: string) => {
  await userEvent.click(screen.getByTestId("create-scim-credential-button"));
  await userEvent.type(
    await screen.findByTestId("scim-credential-name-input"),
    name,
  );
  await userEvent.click(screen.getByTestId("scim-credential-provider-input"));
  await userEvent.click(await screen.findByRole("option", { name: "Okta" }));
  await userEvent.click(screen.getByTestId("scim-credential-submit-button"));
};

const expectTokenOutsideMutationCache = (token: string) => {
  const mutationStates = queryClient
    .getMutationCache()
    .getAll()
    .map(({ state }) => state);
  expect(JSON.stringify(mutationStates)).not.toContain(token);
};

describe("SCIM settings route", () => {
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
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(SAAS_CONFIG);
    vi.spyOn(organizationService, "getMe").mockResolvedValue(
      userWithRole("member", true),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetOrgMockData();
    resetScimMockData();
    useSelectedOrganizationStore.setState({ organizationId: null });
    mockQueryClient.clear();
    queryClient.clear();
    mockDisplayErrorToast.mockClear();
  });

  it("shows the SCIM base URL and the credential metadata", async () => {
    renderPage();

    const row = await findRow("Okta production");
    expect(await screen.findByTestId("scim-base-url")).toHaveTextContent(
      `${window.location.origin}/scim/v2`,
    );
    expect(within(row).getByTestId("scim-credential-status")).toHaveTextContent(
      "SCIM$ACTIVE_CHIP",
    );
    expect(
      within(row).getByTestId("scim-credential-details"),
    ).toHaveTextContent("Okta");
    expect(
      within(row).getByTestId("scim-credential-details"),
    ).toHaveTextContent("SCIM$LAST_USED_AT");
  });

  it("shows a new token once and never again", async () => {
    const { unmount } = renderPage();
    await findRow("Okta production");

    await userEvent.click(screen.getByTestId("create-scim-credential-button"));
    await userEvent.type(
      await screen.findByTestId("scim-credential-name-input"),
      "Okta rotation",
    );
    // A Super Admin is offered every provider and has to choose one. The
    // one the org is not bound to yet says so.
    await userEvent.click(screen.getByTestId("scim-credential-provider-input"));
    expect(
      await screen.findByRole("option", {
        name: 'SCIM$PROVIDER_NEW_BINDING {"title":"Microsoft Entra ID"}',
      }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("option", { name: "Okta" }));
    await userEvent.click(screen.getByTestId("scim-credential-submit-button"));
    const token =
      (await screen.findByTestId("scim-token-value")).textContent ?? "";

    expect(token).toMatch(/^ohscim_/);
    // Escape must not lose the only copy of the token.
    await userEvent.keyboard("{Escape}");
    expect(screen.getByTestId("scim-token-modal")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("scim-token-done-button"));
    await waitFor(() =>
      expect(screen.queryByTestId("scim-token-modal")).not.toBeInTheDocument(),
    );
    const row = await findRow("Okta rotation");
    expect(
      within(row).getByTestId("scim-credential-details"),
    ).toHaveTextContent("SCIM$NEVER_USED");
    expect(document.body).not.toHaveTextContent(token);
    expect(JSON.stringify(queryClient.getQueryCache().getAll())).not.toContain(
      token,
    );
    await waitFor(() => expect(queryClient.isMutating()).toBe(0));
    expectTokenOutsideMutationCache(token);

    unmount();
    expectTokenOutsideMutationCache(token);
  });

  it("requires a name and a provider before creating", async () => {
    setScimMockCredentials(MOCK_TEAM_ORG_ACME.id, []);
    renderPage();
    await screen.findByTestId("scim-empty");

    await userEvent.click(screen.getByTestId("create-scim-credential-button"));
    const submit = await screen.findByTestId("scim-credential-submit-button");
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("scim-credential-name-input"),
      "  ",
    );
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("scim-credential-name-input"),
      "Okta",
    );
    // A Super Admin is offered two providers here, and none is preselected.
    expect(submit).toBeDisabled();
  });

  it("revokes a credential after confirmation", async () => {
    renderPage();
    const row = await findRow("Okta production");

    const revoke = within(row).getByTestId("revoke-scim-credential");
    expect(revoke).toHaveAccessibleName(
      'SCIM$REVOKE_LABEL {"name":"Okta production"}',
    );
    await userEvent.click(revoke);
    const confirmation = await screen.findByTestId(
      "revoke-scim-credential-modal",
    );
    expect(
      within(confirmation).getByText(
        'SCIM$REVOKE_WARNING {"name":"Okta production"}',
      ),
    ).toBeInTheDocument();
    await userEvent.click(within(confirmation).getByTestId("confirm-button"));

    await waitFor(async () =>
      expect(
        within(await findRow("Okta production")).getByTestId(
          "scim-credential-status",
        ),
      ).toHaveTextContent("SCIM$REVOKED_CHIP"),
    );
    expect(
      within(await findRow("Okta production")).queryByTestId(
        "revoke-scim-credential",
      ),
    ).not.toBeInTheDocument();
  });

  it("keeps the credential when the revocation is cancelled", async () => {
    renderPage();
    const row = await findRow("Okta production");

    await userEvent.click(within(row).getByTestId("revoke-scim-credential"));
    await userEvent.click(await screen.findByTestId("cancel-button"));

    expect(
      within(await findRow("Okta production")).getByTestId(
        "scim-credential-status",
      ),
    ).toHaveTextContent("SCIM$ACTIVE_CHIP");
  });

  it("toasts the server's reason when a create is refused", async () => {
    server.use(
      http.post(credentialsUrl, () =>
        HttpResponse.json({ detail: "Refused by the server" }, { status: 403 }),
      ),
    );
    renderPage();
    await findRow("Okta production");

    await userEvent.click(screen.getByTestId("create-scim-credential-button"));
    await userEvent.type(
      await screen.findByTestId("scim-credential-name-input"),
      "Okta rotation",
    );
    await userEvent.click(screen.getByTestId("scim-credential-provider-input"));
    await userEvent.click(await screen.findByRole("option", { name: "Okta" }));
    await userEvent.click(screen.getByTestId("scim-credential-submit-button"));

    await waitFor(() =>
      expect(mockDisplayErrorToast).toHaveBeenCalledWith(
        "Refused by the server",
      ),
    );
    expect(screen.queryByTestId("scim-token-modal")).not.toBeInTheDocument();
  });

  it("shows an error when the setup cannot be loaded", async () => {
    server.use(
      http.get(credentialsUrl, () =>
        HttpResponse.json({ detail: "Not found" }, { status: 404 }),
      ),
    );
    renderPage();

    expect(await screen.findByTestId("scim-load-error")).toBeInTheDocument();
  });

  it.each(["owner", "admin", "member"] as const)(
    "redirects an org %s without requesting SCIM data",
    async (role) => {
      const getSetup = vi.spyOn(scimService, "getSetup");
      const listCredentials = vi.spyOn(scimService, "listCredentials");
      vi.spyOn(organizationService, "getMe").mockResolvedValue(
        userWithRole(role),
      );
      renderPage();

      expect(await screen.findByTestId("settings-screen")).toBeInTheDocument();
      expect(screen.queryByTestId("scim-settings")).not.toBeInTheDocument();
      expect(getSetup).not.toHaveBeenCalled();
      expect(listCredentials).not.toHaveBeenCalled();
    },
  );

  it("redirects a Super Admin away while enable_scim is off", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue({
      ...SAAS_CONFIG,
      feature_flags: { ...SAAS_CONFIG.feature_flags, enable_scim: false },
    });
    renderPage();

    expect(
      await screen.findByTestId("organizations-screen"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("scim-settings")).not.toBeInTheDocument();
  });

  it("shows an error, not a false success, when copying the base URL fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    mockClipboard(writeText);
    renderPage();
    await findRow("Okta production");

    await userEvent.click(screen.getByTestId("copy-scim-base-url"));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(mockDisplayErrorToast).toHaveBeenCalledWith(
        "SCIM$BASE_URL_COPY_FAILED",
      ),
    );
  });

  it("does not carry org A's token modal onto org B's screen after a switch", async () => {
    renderPage();
    await findRow("Okta production");

    await submitCredential("Acme rotation");
    const token =
      (await screen.findByTestId("scim-token-value")).textContent ?? "";
    expect(token).toMatch(/^ohscim_/);
    expect(screen.getByTestId("scim-token-modal")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("link", { name: "Open Beta SCIM" }));
    expect(
      await screen.findByText(MOCK_TEAM_ORG_BETA.name),
    ).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.queryByTestId("scim-token-modal")).not.toBeInTheDocument(),
    );
    expect(document.body).not.toHaveTextContent(token);
  });

  it("does not show a token when create finishes after switching organizations", async () => {
    const token = "ohscim_mock-delayed-acme-token";
    const createStarted = vi.fn();
    let releaseCreate!: () => void;
    const createResponse = new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    server.use(
      http.post(credentialsUrl, async () => {
        createStarted();
        await createResponse;
        return HttpResponse.json(
          {
            id: "delayed-acme-credential",
            name: "Acme rotation",
            auth_provider_id: 7,
            auth_provider_title: "Okta",
            created_at: "2026-11-24T00:00:00Z",
            last_used_at: null,
            revoked_at: null,
            token,
          },
          { status: 201 },
        );
      }),
    );
    renderPage();
    await findRow("Okta production");

    try {
      await submitCredential("Acme rotation");
      await waitFor(() => expect(createStarted).toHaveBeenCalledTimes(1));
      await userEvent.click(
        screen.getByRole("link", { name: "Open Beta SCIM" }),
      );
      expect(
        await screen.findByText(MOCK_TEAM_ORG_BETA.name),
      ).toBeInTheDocument();
      expect(queryClient.isMutating()).toBe(1);
    } finally {
      releaseCreate();
    }

    await waitFor(() => {
      expect(
        queryClient
          .getMutationCache()
          .getAll()
          .map(({ state }) => state.status),
      ).toEqual(["success"]);
    });
    expect(screen.queryByTestId("scim-token-modal")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(token);
    expectTokenOutsideMutationCache(token);
  });
});
