import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  renderWithProviders,
  createAxiosNotFoundErrorObject,
} from "test-utils";
import { createRoutesStub } from "react-router";
import { screen, waitFor } from "@testing-library/react";
import { Sidebar } from "#/components/features/sidebar/sidebar";
import SettingsService from "#/api/settings-service/settings-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { MOCK_DEFAULT_USER_SETTINGS } from "#/mocks/handlers";
import { WebClientConfig } from "#/api/option-service/option.types";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const createMockConfig = (
  overrides: Omit<Partial<WebClientConfig>, "feature_flags"> & {
    feature_flags?: Partial<WebClientConfig["feature_flags"]>;
  } = {},
): WebClientConfig => {
  const { feature_flags: featureFlagOverrides, ...restOverrides } = overrides;
  return {
    app_mode: "oss",
    posthog_client_key: "test-posthog-key",
    feature_flags: {
      enable_billing: false,
      hide_llm_settings: false,
      enable_jira: false,
      enable_jira_dc: false,
      enable_linear: false,
      hide_users_page: false,
      hide_billing_page: false,
      hide_integrations_page: false,
      ...featureFlagOverrides,
    },
    providers_configured: [],
    maintenance_start_time: null,
    auth_url: null,
    recaptcha_site_key: null,
    faulty_models: [],
    error_message: null,
    updated_at: "2024-01-14T10:00:00Z",
    github_app_slug: null,
    ...restOverrides,
  };
};

// The conversation panel renders through a portal, so it is not a child of Sidebar.

const ConversationRouterStub = createRoutesStub([
  {
    path: "/conversation/:conversationId",
    Component: () => <Sidebar />,
  },
]);

const SettingsRouterStub = createRoutesStub([
  {
    path: "/settings",
    Component: () => <Sidebar />,
  },
]);

const renderSidebar = (path: "conversation" | "settings" = "conversation") => {
  if (path === "settings") {
    return renderWithProviders(
      <SettingsRouterStub initialEntries={["/settings"]} />,
    );
  }
  return renderWithProviders(
    <ConversationRouterStub initialEntries={["/conversation/123"]} />,
  );
};

describe("Sidebar", () => {
  const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");
  const getConfigSpy = vi.spyOn(OptionService, "getConfig");

  beforeEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: "test-org-id" });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("should fetch settings data on mount", async () => {
    renderSidebar();
    await waitFor(() => expect(getSettingsSpy).toHaveBeenCalled());
  });

  describe("Settings modal auto-open behavior", () => {
    it("should NOT open settings modal when hide_llm_settings is true even with 404 error", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({ feature_flags: { hide_llm_settings: true } }),
      );
      getSettingsSpy.mockRejectedValue(createAxiosNotFoundErrorObject());

      renderSidebar();

      await waitFor(() => {
        expect(getConfigSpy).toHaveBeenCalled();
        expect(getSettingsSpy).toHaveBeenCalled();
      });

      await waitFor(() => {
        expect(screen.queryByTestId("ai-config-modal")).not.toBeInTheDocument();
      });
    });

    it("should open settings modal when hide_llm_settings is false and 404 error in OSS mode", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({ feature_flags: { hide_llm_settings: false } }),
      );
      getSettingsSpy.mockRejectedValue(createAxiosNotFoundErrorObject());

      renderSidebar();

      await waitFor(() => {
        expect(screen.getByTestId("ai-config-modal")).toBeInTheDocument();
      });
    });

    it("should NOT open settings modal in SaaS mode even with 404 error", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({
          app_mode: "saas",
          feature_flags: { hide_llm_settings: false },
        }),
      );
      getSettingsSpy.mockRejectedValue(createAxiosNotFoundErrorObject());

      renderSidebar();

      await waitFor(() => {
        expect(getConfigSpy).toHaveBeenCalled();
        expect(getSettingsSpy).toHaveBeenCalled();
      });

      await waitFor(() => {
        expect(screen.queryByTestId("ai-config-modal")).not.toBeInTheDocument();
      });
    });

    it("should NOT open settings modal when settings exist (no 404 error)", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({ feature_flags: { hide_llm_settings: false } }),
      );
      getSettingsSpy.mockResolvedValue(MOCK_DEFAULT_USER_SETTINGS);

      renderSidebar();

      await waitFor(() => {
        expect(getConfigSpy).toHaveBeenCalled();
        expect(getSettingsSpy).toHaveBeenCalled();
      });

      await waitFor(() => {
        expect(screen.queryByTestId("ai-config-modal")).not.toBeInTheDocument();
      });
    });

    it("should NOT open settings modal when on /settings path", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({ feature_flags: { hide_llm_settings: false } }),
      );
      getSettingsSpy.mockRejectedValue(createAxiosNotFoundErrorObject());

      renderSidebar("settings");

      await waitFor(() => {
        expect(getConfigSpy).toHaveBeenCalled();
        expect(getSettingsSpy).toHaveBeenCalled();
      });

      // Prevents the modal from showing when the user is already viewing settings
      await waitFor(() => {
        expect(screen.queryByTestId("ai-config-modal")).not.toBeInTheDocument();
      });
    });
  });

  describe("Automations button visibility", () => {
    it("should show automations button when feature_flags.enable_automations is true", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({ feature_flags: { enable_automations: true } }),
      );

      renderSidebar();

      await waitFor(() => {
        expect(
          screen.getByTestId("automations-button"),
        ).toBeInTheDocument();
      });
    });

    it("should hide automations button when feature_flags.enable_automations is false", async () => {
      getConfigSpy.mockResolvedValue(
        createMockConfig({ feature_flags: { enable_automations: false } }),
      );

      renderSidebar();

      await waitFor(() => expect(getSettingsSpy).toHaveBeenCalled());

      expect(
        screen.queryByTestId("automations-button"),
      ).not.toBeInTheDocument();
    });
  });
});
