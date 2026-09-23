import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LegacyResolversPage } from "#/components/features/integrations-hub/legacy-resolvers-page";
import { LEGACY_RESOLVERS } from "#/components/features/integrations-hub/legacy-resolvers";

const mockUseUserProviders = vi.hoisted(() =>
  vi.fn(() => ({ providers: ["github", "gitlab"] as string[] })),
);

const mockUseIntegrationStatus = vi.hoisted(() =>
  vi.fn((platform: string) => {
    if (platform === "jira") {
      return { data: { status: "active", workspace: "acme" } };
    }
    return { data: null };
  }),
);

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("#/hooks/use-user-providers", () => ({
  useUserProviders: () => mockUseUserProviders(),
}));

vi.mock("#/hooks/query/use-integration-status", () => ({
  useIntegrationStatus: (platform: "jira" | "jira-dc" | "linear") =>
    mockUseIntegrationStatus(platform),
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({
    data: {
      app_mode: "saas",
      github_app_slug: "openhands",
      auth_url: "https://example.com/auth",
    },
  }),
}));

vi.mock("#/hooks/use-auth-url", () => ({
  useAuthUrl: () => "https://example.com/oauth/gitlab",
}));

vi.mock("#/hooks/mutation/use-link-integration", () => ({
  useLinkIntegration: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("#/hooks/mutation/use-unlink-integration", () => ({
  useUnlinkIntegration: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("#/hooks/mutation/use-configure-integration", () => ({
  useConfigureIntegration: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("#/utils/custom-toast-handlers", () => ({
  displayErrorToast: vi.fn(),
  displaySuccessToast: vi.fn(),
}));

vi.mock(
  "#/components/features/settings/git-settings/gitlab-webhook-manager",
  () => ({
    GitLabWebhookManager: () => (
      <div data-testid="gitlab-webhook-manager">webhooks</div>
    ),
  }),
);

vi.mock(
  "#/components/features/settings/git-settings/azure-devops-webhook-manager",
  () => ({
    AzureDevOpsWebhookManager: () => (
      <div data-testid="azure-devops-webhook-manager">webhooks</div>
    ),
  }),
);

vi.mock(
  "#/components/features/settings/git-settings/bitbucket-dc-webhook-manager",
  () => ({
    BitbucketDCWebhookManager: () => (
      <div data-testid="bitbucket-dc-webhook-manager">webhooks</div>
    ),
  }),
);

vi.mock(
  "#/components/features/settings/project-management/jira-dc-integration-panel",
  () => ({
    JiraDcIntegrationPanel: ({
      directConfigure,
    }: {
      directConfigure?: boolean;
    }) =>
      directConfigure ? (
        <div data-testid="jira-dc-configure-modal">jira-dc-configure</div>
      ) : (
        <div data-testid="jira-dc-integration-panel">jira-dc</div>
      ),
  }),
);

vi.mock(
  "#/components/features/settings/project-management/configure-modal",
  () => ({
    ConfigureModal: ({ isOpen }: { isOpen: boolean }) =>
      isOpen ? <div data-testid="project-management-configure-modal" /> : null,
  }),
);

vi.mock("#/routes/git-settings", () => ({
  GitSettingsScreen: ({ focusProvider }: { focusProvider?: string }) => (
    <div data-testid="git-settings-screen">{focusProvider}</div>
  ),
}));

describe("LegacyResolversPage", () => {
  beforeEach(() => {
    mockUseUserProviders.mockReturnValue({
      providers: ["github", "gitlab"],
    });
    mockUseIntegrationStatus.mockImplementation((platform: string) => {
      if (platform === "jira") {
        return { data: { status: "active", workspace: "acme" } };
      }
      return { data: null };
    });
  });

  it("lists every legacy resolver and shows connected labels", () => {
    render(
      <MemoryRouter>
        <LegacyResolversPage />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("integrations-hub-resolvers")).toBeInTheDocument();

    for (const resolver of LEGACY_RESOLVERS) {
      expect(
        screen.getByTestId(`integrations-hub-resolver-row-${resolver.id}`),
      ).toHaveTextContent(resolver.sublineKey);
    }

    expect(
      screen.getByTestId("integrations-hub-resolver-connected-github"),
    ).toHaveTextContent("STATUS$CONNECTED");
    expect(
      screen.getByTestId("integrations-hub-resolver-connected-jira"),
    ).toHaveTextContent("STATUS$CONNECTED");
  });

  it("activates the resolver from the row container or the action button", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    render(
      <MemoryRouter>
        <LegacyResolversPage />
      </MemoryRouter>,
    );

    expect(
      screen.getByTestId("integrations-hub-resolver-action-slack"),
    ).toHaveTextContent("SETTINGS$INSTALL");
    expect(
      screen.getByTestId("integrations-hub-resolver-action-bitbucket"),
    ).toHaveTextContent("SETTINGS$CONNECT");
    expect(
      screen.getByTestId("integrations-hub-resolver-action-forgejo"),
    ).toHaveTextContent("SETTINGS$CONNECT");
    expect(
      screen.getByTestId(
        "integrations-hub-resolver-action-bitbucket_data_center",
      ),
    ).toHaveTextContent("SETTINGS$CONNECT");
    expect(
      screen.getByTestId("integrations-hub-resolver-action-jira"),
    ).toHaveTextContent("PROJECT_MANAGEMENT$EDIT_BUTTON_LABEL");
    expect(
      screen.getByTestId("integrations-hub-resolver-action-linear"),
    ).toHaveTextContent("SETTINGS$CONNECT");
    expect(
      screen.getByTestId("integrations-hub-resolver-action-jira-dc"),
    ).toHaveTextContent("SETTINGS$CONNECT");
    expect(
      screen.getByTestId("integrations-hub-resolver-action-github"),
    ).toHaveTextContent("PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL");

    await user.click(screen.getByTestId("integrations-hub-resolver-row-gitlab"));
    expect(
      screen.getByTestId("integrations-hub-resolver-modal-gitlab"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("gitlab-webhook-manager")).toBeInTheDocument();

    await user.click(screen.getByTestId("integrations-hub-resolver-modal-gitlab-close"));

    await user.click(
      screen.getByTestId("integrations-hub-resolver-action-bitbucket"),
    );
    expect(
      screen.getByTestId("integrations-hub-resolver-modal-bitbucket"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("git-settings-screen")).toHaveTextContent(
      "bitbucket",
    );
    await user.click(
      screen.getByTestId("integrations-hub-resolver-modal-bitbucket-close"),
    );

    await user.click(
      screen.getByTestId("integrations-hub-resolver-action-jira"),
    );
    expect(
      screen.getByTestId("project-management-configure-modal"),
    ).toBeInTheDocument();

    await user.click(
      screen.getByTestId("integrations-hub-resolver-action-linear"),
    );
    expect(
      screen.getByTestId("project-management-configure-modal"),
    ).toBeInTheDocument();

    await user.click(
      screen.getByTestId("integrations-hub-resolver-action-jira-dc"),
    );
    expect(screen.getByTestId("jira-dc-configure-modal")).toBeInTheDocument();

    await user.click(
      screen.getByTestId("integrations-hub-resolver-action-slack"),
    );
    expect(openSpy).toHaveBeenCalledWith(
      "/slack/install",
      "_blank",
      "noreferrer noopener",
    );

    openSpy.mockRestore();
  });
});
