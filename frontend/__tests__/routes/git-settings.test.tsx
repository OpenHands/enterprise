import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import i18next from "i18next";
import { I18nextProvider } from "react-i18next";
import GitSettingsScreen, { clientLoader } from "#/routes/git-settings";
import SettingsService from "#/api/settings-service/settings-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { MOCK_DEFAULT_USER_SETTINGS } from "#/mocks/handlers";
import { WebClientConfig } from "#/api/option-service/option.types";
import * as ToastHandlers from "#/utils/custom-toast-handlers";
import { SecretsService } from "#/api/secrets-service";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const VALID_OSS_CONFIG: WebClientConfig = {
  app_mode: "oss",
  posthog_client_key: "456",
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
  updated_at: "2024-01-14T10:00:00Z",
  github_app_slug: null,
  gitlab_enabled: false,
  slack_enabled: false,
};

const VALID_SAAS_CONFIG: WebClientConfig = {
  app_mode: "saas",
  posthog_client_key: "456",
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
  updated_at: "2024-01-14T10:00:00Z",
  github_app_slug: null,
  gitlab_enabled: false,
  slack_enabled: false,
};

const queryClient = new QueryClient();

const GitSettingsRouterStub = createRoutesStub([
  {
    Component: GitSettingsScreen,
    path: "/settings/integrations",
  },
]);

const renderGitSettingsScreen = () => {
  // Initialize i18next instance
  i18next.init({
    lng: "en",
    resources: {
      en: {
        translation: {
          GITHUB$TOKEN_HELP_TEXT: "Help text",
          GITHUB$TOKEN_LABEL: "GitHub Token",
          GITHUB$HOST_LABEL: "GitHub Host",
          GITLAB$TOKEN_LABEL: "GitLab Token",
          GITLAB$HOST_LABEL: "GitLab Host",
          BITBUCKET$TOKEN_LABEL: "Bitbucket Token",
          BITBUCKET$HOST_LABEL: "Bitbucket Host",
          SETTINGS$GITLAB: "GitLab",
          COMMON$STATUS: "Status",
          STATUS$CONNECTED: "Connected",
          SETTINGS$GITLAB_NOT_CONNECTED: "Not Connected",
          SETTINGS$CONNECT: "Connect",
          SETTINGS$INSTALL: "Install",
          GITLAB$CONNECT_TO_GITLAB: "Log in with GitLab",
          PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL: "Configure",
          SETTINGS$GITLAB_REINSTALL_WEBHOOK: "Reinstall Webhook",
          SETTINGS$GITLAB_INSTALLING_WEBHOOK:
            "Installing GitLab webhook, please wait a few minutes.",
          SETTINGS$SAVING: "Saving...",
          ERROR$GENERIC: "An error occurred",
        },
      },
    },
  });

  const { rerender, ...rest } = render(
    <GitSettingsRouterStub initialEntries={["/settings/integrations"]} />,
    {
      wrapper: ({ children }) => (
        <I18nextProvider i18n={i18next}>
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        </I18nextProvider>
      ),
    },
  );

  const rerenderGitSettingsScreen = () =>
    rerender(
      <I18nextProvider i18n={i18next}>
        <QueryClientProvider client={queryClient}>
          <GitSettingsRouterStub initialEntries={["/settings/integrations"]} />
        </QueryClientProvider>
      </I18nextProvider>,
    );

  return {
    ...rest,
    rerender: rerenderGitSettingsScreen,
  };
};

beforeEach(() => {
  // Since we don't recreate the query client on every test, we need to
  // reset the query client before each test to avoid state leaks
  // between tests.
  queryClient.invalidateQueries();
  useSelectedOrganizationStore.setState({ organizationId: null });
});

const mockOssSettings = () => {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(VALID_OSS_CONFIG);
  vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
    ...MOCK_DEFAULT_USER_SETTINGS,
    provider_tokens_set: {},
  });
};

describe("Content", () => {
  it("should render", async () => {
    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");
  });

  it("should render the inputs if OSS mode", async () => {
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);

    const { rerender } = renderGitSettingsScreen();

    await screen.findByTestId("github-token-input");
    await screen.findByTestId("github-token-help-anchor");

    await screen.findByTestId("gitlab-token-input");
    await screen.findByTestId("gitlab-token-help-anchor");

    await screen.findByTestId("bitbucket-token-input");
    await screen.findByTestId("bitbucket-token-help-anchor");

    await screen.findByTestId("azure-devops-token-input");
    await screen.findByTestId("azure-devops-token-help-anchor");

    getConfigSpy.mockResolvedValue(VALID_SAAS_CONFIG);
    queryClient.invalidateQueries();
    rerender();

    await waitFor(() => {
      expect(
        screen.queryByTestId("github-token-input"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("github-token-help-anchor"),
      ).not.toBeInTheDocument();

      expect(
        screen.queryByTestId("gitlab-token-input"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("gitlab-token-help-anchor"),
      ).not.toBeInTheDocument();

      expect(
        screen.queryByTestId("bitbucket-token-input"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("bitbucket-token-help-anchor"),
      ).not.toBeInTheDocument();

      expect(
        screen.queryByTestId("azure-devops-token-input"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("azure-devops-token-help-anchor"),
      ).not.toBeInTheDocument();
    });
  });

  it("should set '<hidden>' placeholder and indicator if the GitHub token is set", async () => {
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
    });

    const { rerender } = renderGitSettingsScreen();

    await waitFor(() => {
      const githubInput = screen.getByTestId("github-token-input");
      expect(githubInput).toHaveProperty("placeholder", "");
      expect(
        screen.queryByTestId("gh-set-token-indicator"),
      ).not.toBeInTheDocument();

      const gitlabInput = screen.getByTestId("gitlab-token-input");
      expect(gitlabInput).toHaveProperty("placeholder", "");
      expect(
        screen.queryByTestId("gl-set-token-indicator"),
      ).not.toBeInTheDocument();
    });

    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {
        github: null,
        gitlab: null,
      },
    });
    queryClient.invalidateQueries();

    rerender();

    await waitFor(() => {
      const githubInput = screen.getByTestId("github-token-input");
      expect(githubInput).toHaveProperty("placeholder", "<hidden>");
      expect(
        screen.queryByTestId("gh-set-token-indicator"),
      ).toBeInTheDocument();

      const gitlabInput = screen.getByTestId("gitlab-token-input");
      expect(gitlabInput).toHaveProperty("placeholder", "<hidden>");
      expect(
        screen.queryByTestId("gl-set-token-indicator"),
      ).toBeInTheDocument();
    });

    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {
        gitlab: null,
      },
    });
    queryClient.invalidateQueries();

    rerender();

    await waitFor(() => {
      const githubInput = screen.getByTestId("github-token-input");
      expect(githubInput).toHaveProperty("placeholder", "");
      expect(
        screen.queryByTestId("gh-set-token-indicator"),
      ).not.toBeInTheDocument();

      const gitlabInput = screen.getByTestId("gitlab-token-input");
      expect(gitlabInput).toHaveProperty("placeholder", "<hidden>");
      expect(
        screen.queryByTestId("gl-set-token-indicator"),
      ).toBeInTheDocument();
    });
  });

  it("should render the 'Configure GitHub Repositories' button if SaaS mode and github_app_slug exists", async () => {
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);
    getSettingsSpy.mockResolvedValue(MOCK_DEFAULT_USER_SETTINGS);

    const { rerender } = renderGitSettingsScreen();

    let button = screen.queryByTestId("configure-github-repositories-button");
    expect(button).not.toBeInTheDocument();

    expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    expect(screen.getByTestId("disconnect-tokens-button")).toBeInTheDocument();

    getConfigSpy.mockResolvedValue(VALID_SAAS_CONFIG);
    queryClient.invalidateQueries();
    rerender();

    await waitFor(() => {
      button = screen.queryByTestId("configure-github-repositories-button");
      expect(button).not.toBeInTheDocument();
      expect(screen.queryByTestId("gitlab-status-text")).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("install-slack-app-button"),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("submit-button")).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("disconnect-tokens-button"),
      ).not.toBeInTheDocument();
    });

    getConfigSpy.mockResolvedValue({
      ...VALID_SAAS_CONFIG,
      providers_configured: ["gitlab"],
      github_app_slug: "test-slug",
      gitlab_enabled: true,
      slack_enabled: true,
    });
    queryClient.invalidateQueries();
    rerender();

    await waitFor(() => {
      button = screen.getByTestId("configure-github-repositories-button");
      expect(button).toBeInTheDocument();
      expect(button).toHaveClass("bg-primary");
      expect(button.querySelector("svg")).not.toBeInTheDocument();
      expect(screen.queryByTestId("github-status-text")).not.toBeInTheDocument();
      expect(screen.getByTestId("gitlab-status-text")).toHaveTextContent(
        /Not Connected|SETTINGS\$GITLAB_NOT_CONNECTED/,
      );
      expect(screen.getByTestId("configure-gitlab-button")).toBeInTheDocument();
      expect(screen.getByTestId("install-slack-app-button")).toBeInTheDocument();
      expect(screen.queryByTestId("submit-button")).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("disconnect-tokens-button"),
      ).not.toBeInTheDocument();
    });
  });

  it("should use secondary style, gear icon, and Connected chip when GitHub is installed", async () => {
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    // SaaS settings queries require a selected org.
    useSelectedOrganizationStore.setState({ organizationId: "org-1" });

    getConfigSpy.mockResolvedValue({
      ...VALID_SAAS_CONFIG,
      github_app_slug: "test-slug",
    });
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: { github: "github.com" },
    });

    renderGitSettingsScreen();

    await waitFor(() => {
      const button = screen.getByTestId("configure-github-repositories-button");
      expect(button).toHaveClass("bg-base-secondary");
      expect(button).toHaveClass("border");
      expect(button.querySelector("svg")).toBeInTheDocument();
      expect(screen.getByTestId("github-status-text")).toHaveTextContent(
        /Connected|STATUS\$CONNECTED/,
      );
    });
  });
});

describe("Form submission", () => {
  const typeTokenAndSave = async (inputTestId: string, token: string) => {
    const input = await screen.findByTestId(inputTestId);
    const submit = await screen.findByTestId("submit-button");

    // fireEvent.change reliably dirty-tracks the neo SettingsInput onChange.
    fireEvent.change(input, { target: { value: token } });
    await waitFor(() => expect(submit).toBeEnabled());
    await userEvent.click(submit);
  };

  it("should save the GitHub token", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    saveProvidersSpy.mockImplementation(() => Promise.resolve(true));
    mockOssSettings();

    renderGitSettingsScreen();
    await typeTokenAndSave("github-token-input", "test-token");

    await waitFor(() => {
      expect(saveProvidersSpy).toHaveBeenCalledWith({
        github: { token: "test-token", host: "" },
        gitlab: { token: "", host: "" },
        bitbucket: { token: "", host: "" },
        bitbucket_data_center: { token: "", host: "" },
        azure_devops: { token: "", host: "" },
        forgejo: { token: "", host: "" },
      });
    });
  });

  it("should save GitLab tokens", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    saveProvidersSpy.mockImplementation(() => Promise.resolve(true));
    mockOssSettings();

    renderGitSettingsScreen();
    await typeTokenAndSave("gitlab-token-input", "test-token");

    await waitFor(() => {
      expect(saveProvidersSpy).toHaveBeenCalledWith({
        github: { token: "", host: "" },
        gitlab: { token: "test-token", host: "" },
        bitbucket: { token: "", host: "" },
        bitbucket_data_center: { token: "", host: "" },
        azure_devops: { token: "", host: "" },
        forgejo: { token: "", host: "" },
      });
    });
  });

  it("should save the Bitbucket token", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    saveProvidersSpy.mockImplementation(() => Promise.resolve(true));
    mockOssSettings();

    renderGitSettingsScreen();
    await typeTokenAndSave("bitbucket-token-input", "test-token");

    await waitFor(() => {
      expect(saveProvidersSpy).toHaveBeenCalledWith({
        github: { token: "", host: "" },
        gitlab: { token: "", host: "" },
        bitbucket: { token: "test-token", host: "" },
        bitbucket_data_center: { token: "", host: "" },
        azure_devops: { token: "", host: "" },
        forgejo: { token: "", host: "" },
      });
    });
  });

  it("should save the Azure DevOps token", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    saveProvidersSpy.mockImplementation(() => Promise.resolve(true));
    mockOssSettings();

    renderGitSettingsScreen();
    await typeTokenAndSave("azure-devops-token-input", "test-token");

    await waitFor(() => {
      expect(saveProvidersSpy).toHaveBeenCalledWith({
        github: { token: "", host: "" },
        gitlab: { token: "", host: "" },
        bitbucket: { token: "", host: "" },
        bitbucket_data_center: { token: "", host: "" },
        azure_devops: { token: "test-token", host: "" },
        forgejo: { token: "", host: "" },
      });
    });
  });

  it("should disable the button if there is no input", async () => {
    mockOssSettings();

    renderGitSettingsScreen();

    const submit = await screen.findByTestId("submit-button");
    expect(submit).toBeDisabled();

    const githubInput = await screen.findByTestId("github-token-input");
    fireEvent.change(githubInput, { target: { value: "test-token" } });

    expect(submit).not.toBeDisabled();

    fireEvent.change(githubInput, { target: { value: "" } });
    expect(submit).toBeDisabled();

    const gitlabInput = await screen.findByTestId("gitlab-token-input");
    fireEvent.change(gitlabInput, { target: { value: "test-token" } });

    expect(submit).not.toBeDisabled();

    fireEvent.change(gitlabInput, { target: { value: "" } });
    expect(submit).toBeDisabled();
  });

  it("should enable a disconnect tokens button if there is at least one token set", async () => {
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {
        github: null,
        gitlab: null,
      },
    });

    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");

    let disconnectButton = await screen.findByTestId(
      "disconnect-tokens-button",
    );
    // When tokens are set (github and gitlab are not null), the button should be enabled
    await waitFor(() => expect(disconnectButton).not.toBeDisabled());

    // Mock settings with no tokens set
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {},
    });
    queryClient.invalidateQueries();

    disconnectButton = await screen.findByTestId("disconnect-tokens-button");
    // When no tokens are set, the button should be disabled
    await waitFor(() => expect(disconnectButton).toBeDisabled());
  });

  it("should delete git providers when pressing the disconnect tokens button", async () => {
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const deleteGitProvidersSpy = vi.spyOn(SecretsService, "deleteGitProviders");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    deleteGitProvidersSpy.mockResolvedValue(true);
    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {
        github: null,
        gitlab: null,
      },
    });

    renderGitSettingsScreen();

    const disconnectButton = await screen.findByTestId(
      "disconnect-tokens-button",
    );
    await waitFor(() => expect(disconnectButton).not.toBeDisabled());
    await userEvent.click(disconnectButton);

    expect(deleteGitProvidersSpy).toHaveBeenCalled();
  });

  // flaky test
  it.skip("should disable the button when submitting changes", async () => {
    const saveSettingsSpy = vi.spyOn(SecretsService, "addGitProvider");
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);

    renderGitSettingsScreen();

    const submit = await screen.findByTestId("submit-button");
    expect(submit).toBeDisabled();

    const githubInput = await screen.findByTestId("github-token-input");
    await userEvent.type(githubInput, "test-token");
    expect(submit).not.toBeDisabled();

    // submit the form
    await userEvent.click(submit);
    expect(saveSettingsSpy).toHaveBeenCalled();

    expect(submit).toHaveTextContent("Saving...");
    expect(submit).toBeDisabled();

    await waitFor(() => expect(submit).toHaveTextContent("Save"));
  });

  it("should disable the button after submitting changes", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    saveProvidersSpy.mockImplementation(() => Promise.resolve(true));
    mockOssSettings();

    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");

    const submit = await screen.findByTestId("submit-button");
    expect(submit).toBeDisabled();

    const githubInput = await screen.findByTestId("github-token-input");
    fireEvent.change(githubInput, { target: { value: "test-token" } });
    expect(submit).not.toBeDisabled();

    // submit the form
    await userEvent.click(submit);
    expect(saveProvidersSpy).toHaveBeenCalled();
    expect(submit).toBeDisabled();

    const gitlabInput = await screen.findByTestId("gitlab-token-input");
    fireEvent.change(gitlabInput, { target: { value: "test-token" } });
    expect(gitlabInput).toHaveValue("test-token");
    expect(submit).not.toBeDisabled();

    // submit the form
    await userEvent.click(submit);
    expect(saveProvidersSpy).toHaveBeenCalled();

    await waitFor(() => expect(submit).toBeDisabled());
  });
});

describe("Status toasts", () => {
  it("should call displaySuccessToast when the settings are saved", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    saveProvidersSpy.mockImplementation(() => Promise.resolve(true));
    mockOssSettings();

    const displaySuccessToastSpy = vi.spyOn(
      ToastHandlers,
      "displaySuccessToast",
    );

    renderGitSettingsScreen();

    // Toggle setting to change
    const githubInput = await screen.findByTestId("github-token-input");
    fireEvent.change(githubInput, { target: { value: "test-token" } });

    const submit = await screen.findByTestId("submit-button");
    await waitFor(() => expect(submit).toBeEnabled());
    await userEvent.click(submit);

    expect(saveProvidersSpy).toHaveBeenCalled();
    await waitFor(() => expect(displaySuccessToastSpy).toHaveBeenCalled());
  });

  it("should call displayErrorToast when the settings fail to save", async () => {
    const saveProvidersSpy = vi.spyOn(SecretsService, "addGitProvider");
    mockOssSettings();

    const displayErrorToastSpy = vi.spyOn(ToastHandlers, "displayErrorToast");

    saveProvidersSpy.mockRejectedValue(new Error("Failed to save settings"));

    renderGitSettingsScreen();

    // Toggle setting to change
    const gitlabInput = await screen.findByTestId("gitlab-token-input");
    fireEvent.change(gitlabInput, { target: { value: "test-token" } });

    const submit = await screen.findByTestId("submit-button");
    await waitFor(() => expect(submit).toBeEnabled());
    await userEvent.click(submit);

    expect(saveProvidersSpy).toHaveBeenCalled();
    expect(displayErrorToastSpy).toHaveBeenCalled();
  });
});

describe("GitLab Webhook Manager Integration", () => {
  it("should not render GitLab webhook manager in OSS mode", async () => {
    // Arrange
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    getConfigSpy.mockResolvedValue(VALID_OSS_CONFIG);

    // Act
    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");

    // Assert
    await waitFor(() => {
      expect(
        screen.queryByText("GITLAB$WEBHOOK_MANAGER_TITLE"),
      ).not.toBeInTheDocument();
    });
  });

  it("should render configured GitLab and Slack sections in SaaS mode without APP_SLUG", async () => {
    // Arrange
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    getConfigSpy.mockResolvedValue({
      ...VALID_SAAS_CONFIG,
      providers_configured: ["gitlab"],
      gitlab_enabled: true,
      slack_enabled: true,
    });
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {},
    });

    // Act
    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");

    // Assert
    await waitFor(() => {
      expect(
        screen.queryByTestId("configure-github-repositories-button"),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("gitlab-status-text")).toBeInTheDocument();
      expect(screen.getByTestId("install-slack-app-button")).toBeInTheDocument();
      expect(
        screen.queryByText("GITLAB$WEBHOOK_MANAGER_TITLE"),
      ).not.toBeInTheDocument();
    });
  });

  it("should not render GitLab or Slack sections when the backend does not enable them", async () => {
    // Arrange
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    getConfigSpy.mockResolvedValue(VALID_SAAS_CONFIG);
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {},
    });

    // Act
    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");

    // Assert
    await waitFor(() => {
      expect(screen.queryByTestId("gitlab-status-text")).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("install-slack-app-button"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("GITLAB$WEBHOOK_MANAGER_TITLE"),
      ).not.toBeInTheDocument();
    });
  });

  it("should not render GitLab webhook manager when the token is not set", async () => {
    // Arrange
    const getConfigSpy = vi.spyOn(OptionService, "getConfig");
    const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");

    getConfigSpy.mockResolvedValue({
      ...VALID_SAAS_CONFIG,
      providers_configured: ["gitlab"],
      gitlab_enabled: true,
    });
    getSettingsSpy.mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: {},
    });

    // Act
    renderGitSettingsScreen();
    await screen.findByTestId("git-settings-screen");

    // Assert
    await waitFor(() => {
      expect(screen.getByTestId("gitlab-status-text")).toBeInTheDocument();
      expect(
        screen.queryByText("GITLAB$WEBHOOK_MANAGER_TITLE"),
      ).not.toBeInTheDocument();
    });
  });
});

describe("clientLoader permission checks", () => {
  it("should export a clientLoader for route protection", () => {
    expect(clientLoader).toBeDefined();
    expect(typeof clientLoader).toBe("function");
  });
});
