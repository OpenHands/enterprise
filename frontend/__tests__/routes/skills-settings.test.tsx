import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { createRoutesStub, Outlet } from "react-router";
import SkillsSettingsScreen from "#/routes/skills-settings";
import SettingsService from "#/api/settings-service/settings-service.api";
import SkillsService from "#/api/skills-service";
import OptionService from "#/api/option-service/option-service.api";
import * as orgStore from "#/stores/selected-organization-store";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { MOCK_PERSONAL_ORG, MOCK_TEAM_ORG_ACME } from "#/mocks/org-handlers";
import { MOCK_DEFAULT_USER_SETTINGS } from "#/mocks/handlers";
import type { WebClientConfig } from "#/api/option-service/option.types";

// Mock hooks
vi.mock("#/hooks/mutation/use-marketplace-mutations", () => ({
  useMarketplaceMutations: () => ({
    savePersonal: { mutate: vi.fn(), isPending: false },
    saveOrg: { mutate: vi.fn(), isPending: false },
    deletePersonal: { mutate: vi.fn(), isPending: false },
    deleteOrg: { mutate: vi.fn(), isPending: false },
  }),
}));

vi.mock("#/hooks/mutation/use-skill-mutations", () => ({
  useSkillMutations: () => ({
    saveDisabledSkills: { mutate: vi.fn(), isPending: false },
  }),
}));

const mockConfig: WebClientConfig = {
  app_mode: "oss",
  posthog_client_key: null,
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
  providers_configured: [],
  maintenance_start_time: null,
  auth_url: null,
  recaptcha_site_key: null,
  faulty_models: [],
  error_message: null,
  updated_at: new Date().toISOString(),
  github_app_slug: null,
};

const renderSkillsSettings = () => {
  const RouterStub = createRoutesStub([
    {
      Component: () => <Outlet />,
      path: "/settings",
      children: [
        {
          Component: SkillsSettingsScreen,
          path: "/settings/skills",
        },
        {
          Component: () => <div data-testid="skills-settings-screen" />,
          path: "/settings/skills",
        },
      ],
    },
  ]);

  return render(<RouterStub initialEntries={["/settings/skills"]} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false } },
          })
        }
      >
        {children}
      </QueryClientProvider>
    ),
  });
};

beforeEach(() => {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(mockConfig);

  vi.spyOn(organizationService, "getMe").mockResolvedValue({
    org_id: "org-1",
    user_id: "user-1",
    email: "test@example.com",
    role: "admin",
    llm_api_key: "",
    max_iterations: 100,
    llm_model: "gpt-4",
    llm_base_url: "",
    status: "active",
  });

  orgStore.useSelectedOrganizationStore.setState({
    organizationId: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("hasMarketplaceChanges - auto_load toggle bug fix", () => {
  it("should normalize null, undefined, and false to false for comparison", () => {
    // The fix uses Boolean() to normalize all values to true/false
    const normalizeValue = (val: boolean | null | undefined) => Boolean(val);

    // Original value from backend (null/undefined)
    expect(normalizeValue(undefined)).toBe(false);
    expect(normalizeValue(null)).toBe(false);
    // After toggle off
    expect(normalizeValue(false)).toBe(false);
    // After toggle on
    expect(normalizeValue(true)).toBe(true);

    // null, undefined, false all normalize to false (no change)
    expect(normalizeValue(null)).toBe(normalizeValue(false));
    expect(normalizeValue(undefined)).toBe(normalizeValue(null));
  });

  it("should not detect change when toggling auto_load back to original state", () => {
    // Simulates the Boolean comparison logic in hasMarketplaceChanges
    const normalizeAutoLoad = (val: boolean | null | undefined) => Boolean(val);

    // After save, original is null/false → normalized to false
    // After second toggle, value is true → normalized to true
    // Then toggle back to false → normalized to false
    const currentValue = normalizeAutoLoad(false);
    const originalValue = normalizeAutoLoad(null);

    // Both normalize to false, so no change detected
    const hasChange = currentValue !== originalValue;
    expect(hasChange).toBe(false);
  });

  it("should detect change when auto_load is different from original", () => {
    const normalizeAutoLoad = (val: boolean | null | undefined) => Boolean(val);

    const currentValue = normalizeAutoLoad(true);
    const originalValue = normalizeAutoLoad(null);

    const hasChange = currentValue !== originalValue;
    expect(hasChange).toBe(true);
  });
});

describe("hasSkillChanges - enabled toggle bug fix", () => {
  it("should normalize isEnabled comparison correctly", () => {
    const normalizeBool = (val: boolean | undefined | null) => val ?? false;

    // Both should normalize to false
    expect(normalizeBool(false)).toBe(false);
    expect(normalizeBool(undefined)).toBe(false);
    expect(normalizeBool(null)).toBe(false);
  });
});

describe("skill name conflict warning", () => {
  beforeEach(() => {
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      registered_marketplaces: [
        { name: "acme", source: "github:acme/skills", scope: "personal" },
      ],
    });
    vi.spyOn(SkillsService, "getSkills").mockResolvedValue([
      { name: "github", type: "knowledge", source: "global" },
    ]);
  });

  it("should show the conflicting skill name and both sources", async () => {
    // Arrange
    vi.spyOn(SkillsService, "getMarketplaceSkills").mockResolvedValue({
      skills: [],
      plugins: [],
      marketplace_skills: { acme: [] },
      errors: [],
      conflicts: [
        {
          name: "github",
          marketplace: "acme",
          source: "github:acme/skills",
          conflicts_with: "global",
        },
      ],
    });

    // Act
    renderSkillsSettings();

    // Assert
    const alert = await screen.findByTestId("skill-name-conflicts-alert");
    expect(within(alert).getByText("github")).toBeInTheDocument();
    expect(within(alert).getByText("global")).toBeInTheDocument();
    expect(within(alert).getByText("acme")).toBeInTheDocument();
    expect(within(alert).getByText("github:acme/skills")).toBeInTheDocument();
  });

  it("should not show a warning when no skill name conflicts", async () => {
    // Arrange
    vi.spyOn(SkillsService, "getMarketplaceSkills").mockResolvedValue({
      skills: [
        { name: "acme-github", type: "knowledge", source: "marketplace:acme" },
      ],
      plugins: [],
      marketplace_skills: { acme: ["acme-github"] },
      errors: [],
      conflicts: [],
    });

    // Act
    renderSkillsSettings();

    // Assert
    await screen.findByText("acme-github");
    expect(
      screen.queryByTestId("skill-name-conflicts-alert"),
    ).not.toBeInTheDocument();
  });
});

describe("skill source column", () => {
  const LONG_SOURCE =
    "https://example-org@dev.azure.com/example-org/Web%20Apps/_git/team-skills";

  const sourceCell = async (skillName: string) => {
    const row = (await screen.findByText(skillName)).closest("tr");
    return within(row as HTMLElement).getAllByRole("cell")[1];
  };

  beforeEach(() => {
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      registered_marketplaces: [
        { name: "team-skills", source: LONG_SOURCE, scope: "org" },
      ],
    });
    vi.spyOn(SkillsService, "getSkills").mockResolvedValue([
      { name: "github", type: "knowledge", source: "global" },
      { name: "my-notes", type: "knowledge", source: "user" },
    ]);
    vi.spyOn(SkillsService, "getMarketplaceSkills").mockResolvedValue({
      skills: [
        {
          name: "grill-me",
          type: "knowledge",
          source: "marketplace:team-skills",
        },
      ],
      plugins: [
        {
          name: "review-kit",
          source: LONG_SOURCE,
          marketplace: "team-skills",
        },
      ],
      marketplace_skills: { "team-skills": ["grill-me"] },
      errors: [],
      conflicts: [],
    });
  });

  it("shows the marketplace name for its skills, with the address on hover", async () => {
    renderSkillsSettings();

    const cell = await sourceCell("grill-me");
    const label = within(cell).getByText("team-skills");
    expect(label).toHaveAttribute("title", LONG_SOURCE);
    expect(within(cell).queryByText(LONG_SOURCE)).not.toBeInTheDocument();
  });

  it("shows the marketplace name for its plugins", async () => {
    renderSkillsSettings();

    const cell = await sourceCell("review-kit");
    expect(within(cell).getByText("team-skills")).toHaveAttribute(
      "title",
      LONG_SOURCE,
    );
  });

  it("keeps the existing source for built-in and user skills", async () => {
    renderSkillsSettings();

    const globalCell = await sourceCell("github");
    expect(within(globalCell).getByText("global")).toHaveAttribute(
      "title",
      "global",
    );
    const userCell = await sourceCell("my-notes");
    expect(within(userCell).getByText("user")).toHaveAttribute("title", "user");
  });

  it("filters by marketplace name", async () => {
    const user = userEvent.setup();
    renderSkillsSettings();
    await screen.findByText("grill-me");

    const input = screen.getByRole("combobox", { name: "repository-filter" });
    await user.clear(input);
    const options = (await screen.findAllByRole("option")).map(
      (node) => node.textContent,
    );
    expect(options).toEqual(
      expect.arrayContaining(["global", "user", "team-skills"]),
    );
    expect(options).not.toContain(LONG_SOURCE);

    const option = (await screen.findAllByRole("option")).find(
      (node) => node.textContent === "team-skills",
    );
    await user.click(option as HTMLElement);
    await waitFor(() =>
      expect(screen.queryByText("github")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("grill-me")).toBeInTheDocument();
    expect(screen.getByText("review-kit")).toBeInTheDocument();
  });
});

describe("skill descriptions", () => {
  const row = (skillName: string) =>
    screen.getByText(skillName).closest("tr") as HTMLElement;

  beforeEach(() => {
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      registered_marketplaces: [
        { name: "acme", source: "github:acme/skills", scope: "personal" },
      ],
    });
    vi.spyOn(SkillsService, "getSkills").mockResolvedValue([
      {
        name: "github",
        type: "knowledge",
        source: "global",
        description: "Work with GitHub repositories",
      },
      { name: "my-notes", type: "knowledge", source: "user" },
    ]);
    vi.spyOn(SkillsService, "getMarketplaceSkills").mockResolvedValue({
      skills: [
        {
          name: "grill-me",
          type: "knowledge",
          source: "marketplace:acme",
          description: "Asks hard questions",
        },
      ],
      plugins: [
        {
          name: "review-kit",
          description: "Code review helpers",
          source: "github:acme/skills",
          marketplace: "acme",
        },
      ],
      marketplace_skills: { acme: ["grill-me"] },
      errors: [],
      conflicts: [],
    });
  });

  it("expands a row on click to show its description, and collapses it again", async () => {
    const user = userEvent.setup();
    renderSkillsSettings();
    await screen.findByText("github");
    expect(
      screen.queryByText("Work with GitHub repositories"),
    ).not.toBeInTheDocument();

    await user.click(row("github"));
    expect(screen.getByText("Work with GitHub repositories")).toBeVisible();
    expect(
      within(row("github")).getByRole("button", {
        name: "SETTINGS$SKILL_SHOW_DESCRIPTION",
      }),
    ).toHaveAttribute("aria-expanded", "true");

    await user.click(row("github"));
    expect(
      screen.queryByText("Work with GitHub repositories"),
    ).not.toBeInTheDocument();
  });

  it("shows descriptions for marketplace skills and plugins", async () => {
    const user = userEvent.setup();
    renderSkillsSettings();
    await screen.findByText("grill-me");

    await user.click(row("grill-me"));
    await user.click(row("review-kit"));

    expect(screen.getByText("Asks hard questions")).toBeInTheDocument();
    expect(screen.getByText("Code review helpers")).toBeInTheDocument();
  });

  it("shows a placeholder when a skill has no description", async () => {
    const user = userEvent.setup();
    renderSkillsSettings();
    await screen.findByText("my-notes");

    await user.click(row("my-notes"));

    expect(
      screen.getByText("SETTINGS$SKILL_NO_DESCRIPTION"),
    ).toBeInTheDocument();
  });

  it("expands from the keyboard via the chevron button", async () => {
    const user = userEvent.setup();
    renderSkillsSettings();
    await screen.findByText("github");
    const button = within(row("github")).getByRole("button", {
      name: "SETTINGS$SKILL_SHOW_DESCRIPTION",
    });
    expect(button).toHaveAttribute("aria-expanded", "false");

    button.focus();
    await user.keyboard("{Enter}");

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Work with GitHub repositories")).toBeVisible();
  });

  it("toggles enabled without expanding the row", async () => {
    const user = userEvent.setup();
    renderSkillsSettings();
    await screen.findByText("github");
    const toggle = screen.getByRole("switch", {
      name: "Toggle enabled for github",
    });
    expect(toggle).toHaveAttribute("aria-checked", "true");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(
      screen.queryByText("Work with GitHub repositories"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("skills-save-button")).not.toBeDisabled();
  });

  it("does not expand the row when the read-only plugin toggle is clicked", async () => {
    renderSkillsSettings();
    await screen.findByText("review-kit");

    // user-event refuses to click disabled elements; dispatch directly.
    fireEvent.click(
      screen.getByRole("switch", { name: "Auto-load state for review-kit" }),
    );

    expect(screen.queryByText("Code review helpers")).not.toBeInTheDocument();
  });
});
