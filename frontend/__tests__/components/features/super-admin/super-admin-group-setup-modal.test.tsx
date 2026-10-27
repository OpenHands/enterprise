import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAxiosError, renderWithProviders } from "test-utils";
import ConfigService from "#/api/config-service/config-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import OrgProfilesService from "#/api/organization-service/org-profiles-service.api";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";
import { SuperAdminGroupSetupModal } from "#/components/features/super-admin/super-admin-group-setup-modal";
import translations from "#/i18n/translation.json";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import {
  markSuperAdminNuxOrgDone,
  readSuperAdminNux,
  resetSuperAdminNux,
} from "#/utils/org/super-admin-nux";

const ORG_ID = "org-1";

const RouterStub = createRoutesStub([
  { path: "/settings/org-defaults", Component: SuperAdminGroupSetupModal },
  {
    path: "/automations/templates",
    Component: () => <div data-testid="automation-templates-page" />,
  },
]);

const renderModal = () =>
  renderWithProviders(
    <RouterStub initialEntries={["/settings/org-defaults"]} />,
  );

function mockConfig(
  allowUserLlmConfiguration?: boolean,
  featureFlags: Record<string, unknown> = {},
) {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      app_mode: "saas",
      feature_flags: {
        ...createMockWebClientConfig().feature_flags,
        allow_user_llm_configuration: allowUserLlmConfiguration,
        ...featureFlags,
      },
    }),
  );
}

async function chooseModel(
  user: ReturnType<typeof userEvent.setup>,
  model?: string,
  providerLabel = "Anthropic",
) {
  await user.click(await screen.findByTestId("sa-nux-llm-provider"));
  await user.click(await screen.findByText(providerLabel));
  const modelButton = screen.getByTestId("sa-nux-llm-model");
  await waitFor(() => expect(modelButton).toBeEnabled());
  await user.click(modelButton);
  if (model) {
    await user.click(
      within(screen.getByTestId("sa-nux-llm-models")).getByText(model),
    );
  }
}

async function openStep(
  user: ReturnType<typeof userEvent.setup>,
  stepId: string,
) {
  const step = await screen.findByTestId(`sa-nux-starter-step-${stepId}`);
  await user.click(step);
  return document.getElementById(step.getAttribute("aria-controls") ?? "");
}

describe("SuperAdminGroupSetupModal", () => {
  beforeEach(() => {
    resetSuperAdminNux();
    markSuperAdminNuxOrgDone({ name: "Acme" });
    useSelectedOrganizationStore.setState({ organizationId: ORG_ID });
    mockConfig();
    vi.spyOn(ConfigService, "searchProviders").mockResolvedValue({
      items: [
        { name: "openhands", verified: true },
        { name: "anthropic", verified: true },
      ],
      next_page_id: null,
    });
    vi.spyOn(ConfigService, "searchModels").mockResolvedValue({
      items: [
        { provider: "anthropic", name: "claude-opus-4-1", verified: false },
        {
          provider: "anthropic",
          name: "claude-legacy",
          verified: false,
          hidden: true,
        },
        { provider: "anthropic", name: "claude-sonnet-4-5", verified: true },
      ],
      next_page_id: null,
    });
    vi.spyOn(organizationService, "saveOrganizationSettings").mockResolvedValue(
      {} as Awaited<
        ReturnType<typeof organizationService.saveOrganizationSettings>
      >,
    );
    vi.spyOn(OrgProfilesService, "saveProfile").mockResolvedValue(undefined);
    vi.spyOn(OrgProfilesService, "activateProfile").mockResolvedValue(
      undefined,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetSuperAdminNux();
  });

  it("tells the admin the basic setup is for the organization they just named", async () => {
    // Arrange
    renderModal();

    // Act
    const body = await screen.findByText("SA_NUX$STARTER_BODY");

    // Assert
    expect(body).toBeVisible();
    expect(translations.SA_NUX$STARTER_BODY.en).toContain(
      "basic setup for this organization",
    );
  });

  it("lists the provider's models from the server, verified models first", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();

    // Act
    await chooseModel(user);

    // Assert
    const options = within(
      screen.getByTestId("sa-nux-llm-models"),
    ).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "claude-sonnet-4-5",
      "claude-opus-4-1",
    ]);
  });

  it("filters the providers by what the admin types", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();
    const providerInput = await screen.findByRole("combobox", {
      name: "LLM$PROVIDER",
    });
    await user.click(providerInput);
    await screen.findByRole("option", { name: "OpenHands" });

    // Act
    await user.keyboard("anth");

    // Assert
    expect(providerInput).toHaveValue("anth");
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Anthropic"]);
  });

  it("keeps Get started disabled until a model is chosen", async () => {
    // Arrange
    renderModal();

    // Act
    const confirm = await screen.findByTestId("sa-nux-starter-confirm");

    // Assert
    expect(confirm).toBeDisabled();
  });

  it("saves the chosen model as the organization's active LLM and closes", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();
    await chooseModel(user, "claude-sonnet-4-5");
    await user.type(screen.getByTestId("sa-nux-llm-api-key"), "sk-test");

    // Act
    await user.click(screen.getByTestId("sa-nux-starter-confirm"));

    // Assert
    await waitFor(() =>
      expect(
        screen.queryByTestId("sa-nux-starter-modal"),
      ).not.toBeInTheDocument(),
    );
    expect(organizationService.saveOrganizationSettings).toHaveBeenCalledWith({
      orgId: ORG_ID,
      settings: {
        agent_settings_diff: {
          llm: {
            model: "anthropic/claude-sonnet-4-5",
            base_url: null,
            api_key: "sk-test",
          },
        },
      },
    });
    expect(OrgProfilesService.saveProfile).toHaveBeenCalledWith(
      ORG_ID,
      "anthropic_claude-sonnet-4-5",
      { include_secrets: true, preserve_existing_api_key: false },
    );
    expect(OrgProfilesService.activateProfile).toHaveBeenCalledWith(
      ORG_ID,
      "anthropic_claude-sonnet-4-5",
    );
    expect(readSuperAdminNux().starterModalPending).toBe(false);
  });

  it("reports the saved LLM to the setup guide, which opens the next step", async () => {
    // Arrange
    const onSetupStep = vi.fn();
    window.addEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onSetupStep);
    const user = userEvent.setup();
    renderModal();
    await chooseModel(user, "claude-sonnet-4-5");

    // Act
    await user.click(screen.getByTestId("sa-nux-starter-confirm"));

    // Assert
    await waitFor(() => expect(onSetupStep).toHaveBeenCalledTimes(1));
    expect((onSetupStep.mock.calls[0][0] as CustomEvent).detail).toEqual({
      id: "add-llm",
    });
    expect(
      screen.queryByTestId("automation-templates-page"),
    ).not.toBeInTheDocument();
    window.removeEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onSetupStep);
  });

  it("shows the server error and stays open when saving fails", async () => {
    // Arrange
    vi.spyOn(OrgProfilesService, "activateProfile").mockRejectedValue(
      createAxiosError(422, "Unprocessable Entity", {
        detail: "Profile could not be activated",
      }),
    );
    const user = userEvent.setup();
    renderModal();
    await chooseModel(user, "claude-sonnet-4-5");

    // Act
    await user.click(screen.getByTestId("sa-nux-starter-confirm"));

    // Assert
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Profile could not be activated",
    );
    expect(screen.getByTestId("sa-nux-starter-modal")).toBeInTheDocument();
    expect(readSuperAdminNux().starterModalPending).toBe(true);
    expect(
      screen.queryByTestId("automation-templates-page"),
    ).not.toBeInTheDocument();
  });

  it("saves nothing when the admin skips", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();
    await chooseModel(user, "claude-sonnet-4-5");

    // Act
    await user.click(screen.getByTestId("sa-nux-starter-skip"));

    // Assert
    expect(organizationService.saveOrganizationSettings).not.toHaveBeenCalled();
    expect(OrgProfilesService.saveProfile).not.toHaveBeenCalled();
    expect(OrgProfilesService.activateProfile).not.toHaveBeenCalled();
    expect(readSuperAdminNux().starterModalPending).toBe(false);
    expect(
      screen.queryByTestId("automation-templates-page"),
    ).not.toBeInTheDocument();
  });

  it("does not ask for an API key when the install turns off user LLM configuration", async () => {
    // Arrange
    mockConfig(false);
    const user = userEvent.setup();
    renderModal();

    // Act
    await chooseModel(user, "claude-sonnet-4-5");

    // Assert
    expect(screen.queryByTestId("sa-nux-llm-api-key")).not.toBeInTheDocument();
  });

  it("lists the openhands provider as OpenHands Cloud when the install has no LiteLLM gateway", async () => {
    // Arrange
    mockConfig(undefined, { enable_litellm: false });
    const user = userEvent.setup();
    renderModal();

    // Act
    await user.click(await screen.findByTestId("sa-nux-llm-provider"));

    // Assert
    const options = screen
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toContain("OpenHands Cloud");
    expect(options).not.toContain("OpenHands");
  });

  it("asks for an API key for OpenHands Cloud when the install has no LiteLLM gateway", async () => {
    // Arrange
    mockConfig(undefined, { enable_litellm: false });
    const user = userEvent.setup();
    renderModal();

    // Act
    await chooseModel(user, "claude-sonnet-4-5", "OpenHands Cloud");

    // Assert
    expect(screen.getByTestId("sa-nux-llm-api-key")).toBeVisible();
  });

  it("tells the admin that Get started saves the LLM and opens the automation step", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();

    // Act
    const panel = await openStep(user, "automation");

    // Assert
    expect(panel).toHaveAttribute("aria-hidden", "false");
    expect(panel).toHaveTextContent("SA_NUX$STARTER_AUTOMATION_DETAIL");
    expect(translations.SA_NUX$STARTER_AUTOMATION_DETAIL.en).toContain(
      "Get started saves your LLM and takes you there",
    );
  });

  it("points the admin to the Setup guide in the lower-right corner for the rest of setup", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();

    // Act
    const panel = await openStep(user, "others");

    // Assert
    expect(panel).toHaveAttribute("aria-hidden", "false");
    expect(panel).toHaveTextContent("SA_NUX$STARTER_OTHERS_DETAIL");
    expect(translations.SA_NUX$STARTER_OTHERS_DETAIL.en).toContain(
      "from the Setup guide in the lower-right corner",
    );
  });
});
