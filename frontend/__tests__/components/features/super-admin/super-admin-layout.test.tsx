import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  superAdminService,
  type SetupState,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminLayout } from "#/components/features/super-admin/super-admin-layout";
import { SuperAdminOverview } from "#/components/features/super-admin/super-admin-pages";
import { SuperAdminSetupGuide } from "#/components/features/super-admin/super-admin-setup-guide";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";
import { resetSuperAdminNux } from "#/utils/org/super-admin-nux";

const mockMe = vi.hoisted(() => ({
  data: { permissions: ["create_organization"] } as {
    permissions?: string[];
  } | null,
  isLoading: false,
  isPending: false,
}));

const mockConfig = vi.hoisted(() => ({
  data: { feature_flags: { enable_super_admin: true } } as {
    feature_flags?: { enable_super_admin?: boolean };
  } | null,
  isLoading: false,
}));

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => mockMe,
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => mockConfig,
}));

vi.mock("#/hooks/query/use-git-user", () => ({
  useGitUser: () => ({
    data: { avatar_url: "https://example.com/avatar.png", login: "neo-user" },
    isFetching: false,
  }),
}));

vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({
    data: { email: "neo@example.com" },
  }),
}));

vi.mock("#/hooks/mutation/use-logout", () => ({
  useLogout: () => ({ mutate: vi.fn() }),
}));

vi.mock("#/hooks/use-app-mode", () => ({
  useAppMode: () => ({ isSaas: true, isEnterpriseCloud: true }),
}));

const GUIDE_STATE: SetupState = {
  wizard_pending: false,
  guide_org_id: "guide-org",
  guide_dismissed: false,
  guide_steps: {
    org_llm: false,
    mcp_server: false,
    automation: false,
    invite: false,
  },
};

function renderSuperAdmin(
  initialPath = SUPER_ADMIN_PATHS.root,
  setupState = GUIDE_STATE,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The first Super Admin's setup state, already loaded from the server.
  vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(setupState);
  queryClient.setQueryData(SUPER_ADMIN_QUERY_KEYS.setupState, setupState);
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin",
      Component: SuperAdminLayout,
      children: [
        { index: true, Component: SuperAdminOverview },
        {
          path: "setup",
          Component: SuperAdminSetupGuide,
        },
        {
          path: "organizations",
          Component: () => <div data-testid="orgs-stub" />,
        },
      ],
    },
    {
      path: "/install",
      Component: () => <div data-testid="install-stub" />,
    },
    {
      path: "/settings",
      Component: () => <div data-testid="settings-fallback" />,
    },
  ]);

  return render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={[initialPath]} />
    </QueryClientProvider>,
  );
}

describe("SuperAdminLayout", () => {
  beforeEach(() => {
    mockMe.data = { permissions: ["manage_super_admins", "create_organization"] };
    mockMe.isLoading = false;
    mockMe.isPending = false;
    mockConfig.data = { feature_flags: { enable_super_admin: true } };
    mockConfig.isLoading = false;
    resetSuperAdminNux();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens the dashboard when the install wizard has not been completed", () => {
    renderSuperAdmin();

    expect(screen.getByTestId("super-admin-dashboard")).toBeInTheDocument();
    expect(screen.queryByTestId("install-stub")).not.toBeInTheDocument();
  });

  it("renders the standalone Super Admin shell without Org Settings", () => {
    renderSuperAdmin();

    expect(screen.getByTestId("super-admin-screen")).toBeInTheDocument();
    expect(screen.getByTestId("super-admin-navbar")).toBeInTheDocument();
    expect(screen.getByTestId("super-admin-dashboard")).toBeInTheDocument();
    expect(
      screen.getAllByTestId("super-admin-setup-widget")[0],
    ).toHaveAttribute("href", SUPER_ADMIN_PATHS.setup);
    expect(
      screen.getAllByTestId("super-admin-setup-container").length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByTestId("super-admin-setup-divider"),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByTestId("super-admin-back-to-settings")[0],
    ).toHaveAttribute("href", "/settings");
    expect(
      screen.getAllByTestId("sidebar-settings-/super-admin").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByTestId("sidebar-settings-/super-admin/organizations")
        .length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByText("SETTINGS$ORG_SETTINGS_HEADER"),
    ).not.toBeInTheDocument();
    expect(screen.getAllByTestId("org-selector").length).toBeGreaterThan(0);
    expect(
      screen.getAllByDisplayValue("SUPER_ADMIN$ORG_MENU").length,
    ).toBeGreaterThan(0);
  });

  it("shows the instance logo in place of the OpenHands mark", async () => {
    // Arrange
    const savedLogo = "data:image/jpeg;base64,c2F2ZWQ=";
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: "Acme",
      logo: savedLogo,
    });

    // Act
    renderSuperAdmin();

    // Assert
    const logos = await screen.findAllByTestId("instance-logo-mark");
    expect(logos[0]).toHaveAttribute("src", savedLogo);
    expect(
      screen.queryByTestId("openhands-brand-mark"),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("SUPER_ADMIN$TITLE").length).toBeGreaterThan(0);
  });

  it("opens the Setup guide from the sidebar widget", async () => {
    const user = userEvent.setup();
    renderSuperAdmin();

    await user.click(screen.getAllByTestId("super-admin-setup-widget")[0]);

    expect(screen.getByTestId("super-admin-setup")).toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-setup-step-add-llm"),
    ).toBeInTheDocument();
  });

  it("hides the Setup guide widget once the guide is dismissed", () => {
    renderSuperAdmin(SUPER_ADMIN_PATHS.root, {
      ...GUIDE_STATE,
      guide_dismissed: true,
      guide_steps: null,
    });

    expect(
      screen.queryByTestId("super-admin-setup-widget"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-setup-container"),
    ).not.toBeInTheDocument();
  });

  it("redirects users without instance Super Admin permission", () => {
    mockMe.data = { permissions: [] };
    renderSuperAdmin();

    expect(screen.getByTestId("settings-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("super-admin-screen")).not.toBeInTheDocument();
  });

  it("redirects when the Super Admin feature flag is off", () => {
    mockConfig.data = { feature_flags: { enable_super_admin: false } };
    renderSuperAdmin();

    expect(screen.getByTestId("settings-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("super-admin-dashboard")).not.toBeInTheDocument();
  });
});
