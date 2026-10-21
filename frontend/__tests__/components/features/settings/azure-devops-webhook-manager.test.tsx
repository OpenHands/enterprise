import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AzureDevOpsWebhookManager } from "#/components/features/settings/git-settings/azure-devops-webhook-manager";
import { integrationService } from "#/api/integration-service/integration-service.api";
import { I18nKey } from "#/i18n/declaration";

vi.mock("#/utils/custom-toast-handlers", () => ({
  displaySuccessToast: vi.fn(),
  displayErrorToast: vi.fn(),
}));

const status = (
  organization: string,
  installed = false,
  {
    canView = true,
    canManage = true,
    recorded = false,
    installedAt = null as string | null,
  } = {},
) => ({
  organization,
  webhook_installed: installed,
  pr_webhook_installed: installed,
  work_item_webhook_installed: installed,
  pr_subscription_id: installed ? `${organization}-pr` : null,
  work_item_subscription_id: installed ? `${organization}-wi` : null,
  webhook_url: "https://app.example.com/integration/azure-devops/events",
  webhook_secret_set: true,
  can_view_webhooks: canView,
  can_manage_webhooks: canManage,
  install_recorded: recorded,
  installed_at: installedAt,
  last_verified_at: recorded ? "2026-10-21T09:00:00Z" : null,
});

function renderManager() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <AzureDevOpsWebhookManager />
    </QueryClientProvider>,
  );
  return client;
}

async function row(organization: string) {
  return within((await screen.findByText(organization)).closest("tr")!);
}

describe("Azure DevOps organization management", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(
      integrationService,
      "getAzureDevOpsOrganizations",
    ).mockResolvedValue({
      organizations: ["Alpha", "Beta"],
    });
    vi.spyOn(integrationService, "getAzureDevOpsResources").mockImplementation(
      async (org) => status(org!, org === "Alpha"),
    );
  });

  it("keeps status and cached responses separate for each organization", async () => {
    const client = renderManager();
    expect(
      await (
        await row("Alpha")
      ).findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED),
    ).toBeInTheDocument();
    expect(
      await (
        await row("Beta")
      ).findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_NOT_INSTALLED),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("azure-devops-webhook-permission-note"),
    ).not.toBeInTheDocument();
    expect(client.getQueryData(["azure-devops-resources", "Alpha"])).toEqual(
      status("Alpha", true),
    );
    expect(client.getQueryData(["azure-devops-resources", "Beta"])).toEqual(
      status("Beta"),
    );
  });

  it("installs only the chosen organization and refreshes its status", async () => {
    const user = userEvent.setup();
    const install = vi
      .spyOn(integrationService, "reinstallAzureDevOpsWebhook")
      .mockImplementation(async (org) => {
        vi.mocked(
          integrationService.getAzureDevOpsResources,
        ).mockImplementation(async (name) => status(name!, true));
        return { ...status(org!, true), success: true, error: null };
      });
    renderManager();
    const beta = await row("Beta");
    await user.click(
      await beta.findByRole("button", {
        name: I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL,
      }),
    );
    expect(install).toHaveBeenCalledExactlyOnceWith("Beta");
    await waitFor(() =>
      expect(
        beta.getByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED),
      ).toBeInTheDocument(),
    );
    expect(
      vi
        .mocked(integrationService.getAzureDevOpsResources)
        .mock.calls.filter(([name]) => name === "Alpha"),
    ).toHaveLength(1);
  });

  it("uninstalls only the selected organization", async () => {
    const user = userEvent.setup();
    const uninstall = vi
      .spyOn(integrationService, "uninstallAzureDevOpsWebhook")
      .mockResolvedValue({ ...status("Alpha"), success: true, error: null });
    renderManager();
    await user.click(
      await (
        await row("Alpha")
      ).findByRole("button", { name: I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL }),
    );
    expect(uninstall).toHaveBeenCalledExactlyOnceWith("Alpha");
  });

  it("shows a member who can't view the hooks the recorded install, without actions", async () => {
    vi.mocked(integrationService.getAzureDevOpsResources).mockImplementation(
      async (org) =>
        status(org!, false, {
          canView: false,
          canManage: false,
          recorded: org === "Alpha",
          installedAt: org === "Alpha" ? "2026-10-20T10:00:00Z" : null,
        }),
    );
    renderManager();
    const alpha = await row("Alpha");
    expect(
      await alpha.findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED),
    ).toBeInTheDocument();
    expect(
      alpha.getByTestId("azure-devops-webhook-install-details"),
    ).toHaveTextContent(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALLED_ON);
    expect(
      alpha.getByTestId("azure-devops-webhook-permission-note"),
    ).toHaveTextContent(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGED_BY_ADMINS);
    expect(alpha.queryByRole("button")).not.toBeInTheDocument();

    const beta = await row("Beta");
    expect(
      await beta.findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_NOT_SET_UP),
    ).toBeInTheDocument();
    expect(
      beta.queryByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_NOT_INSTALLED),
    ).not.toBeInTheDocument();
    expect(
      beta.queryByTestId("azure-devops-webhook-install-details"),
    ).not.toBeInTheDocument();
    expect(beta.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows a view-only member the live status and install date, without actions", async () => {
    vi.mocked(integrationService.getAzureDevOpsResources).mockImplementation(
      async (org) =>
        status(org!, true, {
          canManage: false,
          recorded: true,
          installedAt: "2026-10-20T10:00:00Z",
        }),
    );
    renderManager();
    const alpha = await row("Alpha");
    expect(
      await alpha.findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED),
    ).toBeInTheDocument();
    expect(
      alpha.getByTestId("azure-devops-webhook-install-details"),
    ).toHaveTextContent(I18nKey.AZURE_DEVOPS$WEBHOOK_LAST_CONFIRMED);
    expect(
      alpha.getByTestId("azure-devops-webhook-permission-note"),
    ).toBeInTheDocument();
    expect(alpha.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows a manager the install date next to the usual actions", async () => {
    vi.mocked(integrationService.getAzureDevOpsResources).mockImplementation(
      async (org) =>
        status(org!, true, {
          recorded: true,
          installedAt: "2026-10-20T10:00:00Z",
        }),
    );
    renderManager();
    const alpha = await row("Alpha");
    expect(
      await alpha.findByTestId("azure-devops-webhook-install-details"),
    ).toHaveTextContent(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALLED_ON);
    expect(
      alpha.getByRole("button", {
        name: I18nKey.AZURE_DEVOPS$WEBHOOK_REINSTALL,
      }),
    ).toBeEnabled();
    expect(
      alpha.getByRole("button", {
        name: I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL,
      }),
    ).toBeEnabled();
    expect(
      alpha.queryByTestId("azure-devops-webhook-permission-note"),
    ).not.toBeInTheDocument();
  });

  it("keeps another organization usable when one status request fails", async () => {
    vi.mocked(integrationService.getAzureDevOpsResources).mockImplementation(
      async (org) => {
        if (org === "Alpha") throw new Error("Access denied");
        return status(org!);
      },
    );
    renderManager();
    expect(
      await (
        await row("Alpha")
      ).findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_ERROR),
    ).toBeInTheDocument();
    expect(
      await (
        await row("Beta")
      ).findByRole("button", { name: I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL }),
    ).toBeEnabled();
  });

  it("does not fall back to a configured organization after discovery fails", async () => {
    vi.mocked(integrationService.getAzureDevOpsOrganizations).mockRejectedValue(
      new Error("Membership unavailable"),
    );
    renderManager();
    expect(
      await screen.findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_ERROR),
    ).toBeInTheDocument();
    expect(integrationService.getAzureDevOpsResources).not.toHaveBeenCalled();
  });

  it("shows a clear empty state when the account has no organizations", async () => {
    vi.mocked(integrationService.getAzureDevOpsOrganizations).mockResolvedValue(
      { organizations: [] },
    );
    renderManager();
    expect(
      await screen.findByText(I18nKey.AZURE_DEVOPS$NO_ORGANIZATIONS),
    ).toBeInTheDocument();
    expect(integrationService.getAzureDevOpsResources).not.toHaveBeenCalled();
  });
});
