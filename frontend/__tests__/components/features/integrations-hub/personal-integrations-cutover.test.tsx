import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PersonalIntegrationsLayout } from "#/components/features/integrations-hub/personal-integrations-layout";
import { PERSONAL_INTEGRATIONS_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";
import { LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY } from "#/components/features/integrations-hub/legacy-integrations-cutover";

const mockOrgTypeAndAccess = vi.hoisted(() => ({
  isPersonalOrg: true,
  isTeamOrg: false,
  organizationId: "personal-1",
  selectedOrg: { id: "personal-1", is_personal: true },
  canViewOrgRoutes: false,
}));

const mockConfig = vi.hoisted(() => ({
  enableIntegrationsHub: true,
}));

const mockProviders = vi.hoisted(() => ({
  providers: ["github", "gitlab"] as string[],
  isLoadingSettings: false,
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

vi.mock("#/hooks/use-user-providers", () => ({
  useUserProviders: () => mockProviders,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string }) =>
      options?.name ? `${key}:${options.name}` : key,
  }),
}));

vi.mock(
  "#/components/features/settings/git-settings/integration-provider-icon",
  () => ({
    IntegrationProviderIcon: ({ provider }: { provider: string }) => (
      <span data-testid={`icon-${provider}`} />
    ),
  }),
);

function renderPersonalLayout() {
  return render(
    <MemoryRouter initialEntries={[PERSONAL_INTEGRATIONS_PATHS.integrations]}>
      <Routes>
        <Route
          path="/settings/integrations"
          element={<PersonalIntegrationsLayout />}
        >
          <Route
            index
            element={<div data-testid="personal-integrations-screen" />}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("PersonalIntegrationsLayout cutover modal", () => {
  beforeEach(() => {
    localStorage.clear();
    mockConfig.enableIntegrationsHub = true;
    mockProviders.providers = ["github", "gitlab"];
    mockProviders.isLoadingSettings = false;
  });

  it("shows the reconnect modal for legacy providers on first Hub visit", () => {
    renderPersonalLayout();

    expect(
      screen.getByTestId("integrations-hub-cutover-modal"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-cutover-item-github"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-cutover-reconnect-github"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-cutover-reconnect-gitlab"),
    ).not.toBeInTheDocument();
  });

  it("dismisses the modal and persists that choice", async () => {
    const user = userEvent.setup();
    renderPersonalLayout();

    await user.click(screen.getByTestId("integrations-hub-cutover-got-it"));

    expect(
      screen.queryByTestId("integrations-hub-cutover-modal"),
    ).not.toBeInTheDocument();
    expect(localStorage.getItem(LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY)).toBe(
      "true",
    );
  });

  it("opens the connect wizard when Reconnect is clicked", async () => {
    const user = userEvent.setup();
    renderPersonalLayout();

    await user.click(
      screen.getByTestId("integrations-hub-cutover-reconnect-github"),
    );

    expect(
      screen.queryByTestId("integrations-hub-cutover-modal"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("integration-wizard-modal")).toBeInTheDocument();
  });

  it("does not show the cutover modal when Hub is disabled", () => {
    mockConfig.enableIntegrationsHub = false;
    renderPersonalLayout();

    expect(
      screen.queryByTestId("integrations-hub-cutover-modal"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("personal-integrations-screen"),
    ).toBeInTheDocument();
  });
});
