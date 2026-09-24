import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuperAdminLayout } from "#/components/features/super-admin/super-admin-layout";
import { SuperAdminOverview } from "#/components/features/super-admin/super-admin-pages";
import { SuperAdminSetupGuide } from "#/components/features/super-admin/super-admin-setup-guide";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import {
  resetSuperAdminSetupState,
  setSuperAdminSetupVisible,
} from "#/components/features/super-admin/super-admin-setup";
import {
  markSuperAdminNuxAccountDone,
  markSuperAdminNuxTosDone,
  markSuperAdminNuxWelcomeDone,
  resetSuperAdminNux,
} from "#/utils/org/super-admin-nux";

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

function renderSuperAdmin(initialPath = SUPER_ADMIN_PATHS.root) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
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
    resetSuperAdminSetupState();
    resetSuperAdminNux();
    markSuperAdminNuxWelcomeDone();
    markSuperAdminNuxTosDone();
    markSuperAdminNuxAccountDone({ name: "Neo", email: "neo@example.com" });
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
    expect(screen.queryByTestId("org-selector")).not.toBeInTheDocument();
  });

  it("opens the Setup guide from the sidebar widget", async () => {
    const user = userEvent.setup();
    renderSuperAdmin();

    await user.click(screen.getAllByTestId("super-admin-setup-widget")[0]);

    expect(screen.getByTestId("super-admin-setup")).toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-setup-step-create-org"),
    ).toBeInTheDocument();
  });

  it("hides the Setup guide widget when it is turned off", () => {
    setSuperAdminSetupVisible(false);
    renderSuperAdmin();

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
