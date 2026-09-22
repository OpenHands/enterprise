import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminOverviewPage } from "#/components/features/integrations-hub/admin-pages";
import { AgentRequestsPage } from "#/components/features/integrations-hub/agent-requests-page";
import { IntegrationsHubLayout } from "#/components/features/integrations-hub/integrations-hub-layout";
import {
  INTEGRATIONS_HUB_PATHS,
  PERSONAL_INTEGRATIONS_PATHS,
} from "#/components/features/integrations-hub/integrations-hub-paths";
import { PersonalIntegrationsLayout } from "#/components/features/integrations-hub/personal-integrations-layout";

const mockOrgTypeAndAccess = vi.hoisted(() => ({
  isPersonalOrg: false,
  isTeamOrg: true,
  organizationId: "org-1",
  selectedOrg: { id: "org-1", is_personal: false },
  canViewOrgRoutes: true,
}));

const mockConfig = vi.hoisted(() => ({
  enableIntegrationsHub: true,
}));

vi.mock("#/hooks/use-org-type-and-access", () => ({
  useOrgTypeAndAccess: () => mockOrgTypeAndAccess,
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({
    data: {
      app_mode: "saas",
      feature_flags: {
        enable_integrations_hub: mockConfig.enableIntegrationsHub,
      },
    },
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

function renderPersonal(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/settings/integrations"
          element={<PersonalIntegrationsLayout />}
        >
          <Route
            index
            element={<div data-testid="personal-integrations-screen" />}
          />
          <Route path="agent-requests" element={<AgentRequestsPage />} />
          <Route
            path="agent-connection"
            element={<div data-testid="personal-integrations-connection" />}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function renderHub(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/settings/integrations"
          element={<div data-testid="personal-integrations-screen" />}
        />
        <Route
          path="/settings/integrations/agent-requests"
          element={<div data-testid="personal-integrations-agent-requests" />}
        />
        <Route
          path="/settings/integrations/agent-connection"
          element={<div data-testid="personal-integrations-connection" />}
        />
        <Route
          path="/settings/integrations-hub"
          element={<IntegrationsHubLayout />}
        >
          <Route
            index
            element={<div data-testid="integrations-hub-screen" />}
          />
          <Route path="agent-requests" element={<div />} />
          <Route path="agent-connection" element={<div />} />
          <Route path="admin-overview" element={<AdminOverviewPage />} />
          <Route
            path="admin-catalog"
            element={<div data-testid="integrations-hub-admin-catalog" />}
          />
          <Route
            path="admin-user-requests"
            element={<div data-testid="integrations-hub-admin-user-requests" />}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("personal Integrations nav", () => {
  beforeEach(() => {
    mockConfig.enableIntegrationsHub = true;
    mockOrgTypeAndAccess.isPersonalOrg = false;
    mockOrgTypeAndAccess.isTeamOrg = true;
  });

  it("renders the personal Integrations rail", () => {
    renderPersonal(PERSONAL_INTEGRATIONS_PATHS.integrations);

    const nav = screen.getByTestId("personal-integrations-navbar");
    expect(nav).toBeInTheDocument();
    expect(screen.getByTestId("integrations-hub-nav-title")).toHaveTextContent(
      "SETTINGS$NAV_INTEGRATIONS",
    );
    expect(
      screen.getByRole("link", { name: "SETTINGS$NAV_INTEGRATIONS" }),
    ).toHaveAttribute("href", PERSONAL_INTEGRATIONS_PATHS.integrations);
    expect(
      screen.getByRole("link", {
        name: /INTEGRATIONS_HUB\$NAV_AGENT_REQUESTS/,
      }),
    ).toHaveAttribute("href", PERSONAL_INTEGRATIONS_PATHS.agentRequests);
    expect(
      screen.getByRole("link", {
        name: /INTEGRATIONS_HUB\$NAV_AGENT_CONNECTION/,
      }),
    ).toHaveAttribute("href", PERSONAL_INTEGRATIONS_PATHS.agentConnection);
    expect(
      screen.queryByRole("link", {
        name: "INTEGRATIONS_HUB$NAV_ADMIN_OVERVIEW",
      }),
    ).not.toBeInTheDocument();

    expect(screen.getByTestId("dashboard-nav-trailing-count")).toHaveTextContent(
      "4",
    );
  });

  it("lists stubbed agent requests on the personal tab", () => {
    renderPersonal(PERSONAL_INTEGRATIONS_PATHS.agentRequests);

    expect(
      screen.getByTestId("integrations-hub-agent-requests"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-approval-apr-1"),
    ).toBeInTheDocument();
  });
});

describe("Integrations Hub admin nav", () => {
  beforeEach(() => {
    mockOrgTypeAndAccess.isPersonalOrg = false;
    mockOrgTypeAndAccess.isTeamOrg = true;
  });

  it("renders only administration links", () => {
    renderHub(INTEGRATIONS_HUB_PATHS.adminOverview);

    const nav = screen.getByTestId("integrations-hub-navbar");
    expect(nav).toBeInTheDocument();
    expect(screen.getByTestId("integrations-hub-nav-title")).toHaveTextContent(
      "SETTINGS$NAV_INTEGRATIONS_HUB",
    );
    expect(
      screen.queryByRole("link", { name: "SETTINGS$NAV_INTEGRATIONS" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: "INTEGRATIONS_HUB$NAV_ADMIN_OVERVIEW",
      }),
    ).toHaveAttribute("href", INTEGRATIONS_HUB_PATHS.adminOverview);
    expect(
      screen.getByRole("link", { name: "INTEGRATIONS_HUB$NAV_ADMIN_CATALOG" }),
    ).toHaveAttribute("href", INTEGRATIONS_HUB_PATHS.adminCatalog);
    expect(
      screen.getByRole("link", {
        name: /INTEGRATIONS_HUB\$NAV_ADMIN_USER_REQUESTS/,
      }),
    ).toHaveAttribute("href", INTEGRATIONS_HUB_PATHS.adminUserRequests);

    const adminLinks = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(adminLinks).toEqual([
      INTEGRATIONS_HUB_PATHS.adminCatalog,
      INTEGRATIONS_HUB_PATHS.adminOverview,
      INTEGRATIONS_HUB_PATHS.adminUserRequests,
    ]);

    expect(screen.getByTestId("dashboard-nav-trailing-count")).toHaveTextContent(
      "3",
    );
  });

  it("collapses the administration rail into a dropdown on mobile", async () => {
    const user = userEvent.setup();
    const originalWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 500,
    });

    renderHub(INTEGRATIONS_HUB_PATHS.adminCatalog);

    const trigger = screen.getByTestId("integrations-hub-nav-mobile-trigger");
    expect(trigger).toHaveTextContent("INTEGRATIONS_HUB$NAV_ADMIN_CATALOG");
    expect(
      screen.queryByTestId("integrations-hub-nav-title"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", {
        name: "INTEGRATIONS_HUB$NAV_ADMIN_OVERVIEW",
      }),
    ).not.toBeInTheDocument();

    await user.click(trigger);

    expect(
      screen.getByRole("link", {
        name: "INTEGRATIONS_HUB$NAV_ADMIN_OVERVIEW",
      }),
    ).toHaveAttribute("href", INTEGRATIONS_HUB_PATHS.adminOverview);
    expect(
      screen.getByRole("link", { name: "INTEGRATIONS_HUB$NAV_ADMIN_CATALOG" }),
    ).toHaveAttribute("href", INTEGRATIONS_HUB_PATHS.adminCatalog);
    expect(
      screen.getByRole("link", {
        name: /INTEGRATIONS_HUB\$NAV_ADMIN_USER_REQUESTS/,
      }),
    ).toHaveAttribute("href", INTEGRATIONS_HUB_PATHS.adminUserRequests);

    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: originalWidth,
    });
  });

  it("redirects personal workspaces away from admin routes", () => {
    mockOrgTypeAndAccess.isPersonalOrg = true;
    mockOrgTypeAndAccess.isTeamOrg = false;
    renderHub(INTEGRATIONS_HUB_PATHS.adminOverview);

    expect(
      screen.getByTestId("personal-integrations-screen"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-admin-overview"),
    ).not.toBeInTheDocument();
  });

  it("redirects legacy Hub personal routes to the personal Integrations tab", () => {
    renderHub("/settings/integrations-hub/agent-requests");

    expect(
      screen.getByTestId("personal-integrations-agent-requests"),
    ).toBeInTheDocument();
  });
});
