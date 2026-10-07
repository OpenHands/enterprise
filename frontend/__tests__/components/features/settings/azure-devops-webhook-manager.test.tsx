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

const status = (organization: string, installed = false) => ({
  organization,
  webhook_installed: installed,
  pr_webhook_installed: installed,
  work_item_webhook_installed: installed,
  pr_subscription_id: installed ? `${organization}-pr` : null,
  work_item_subscription_id: installed ? `${organization}-wi` : null,
  webhook_url: "https://app.example.com/integration/azure-devops/events",
  webhook_secret_set: true,
});

function renderManager() {
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false }, mutations: { retry: false },
  } });
  render(<QueryClientProvider client={client}><AzureDevOpsWebhookManager /></QueryClientProvider>);
  return client;
}

async function row(organization: string) {
  return within((await screen.findByText(organization)).closest("tr")!);
}

describe("Azure DevOps organization management", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(integrationService, "getAzureDevOpsOrganizations").mockResolvedValue({
      organizations: ["Alpha", "Beta"], default_organization: "Alpha",
    });
    vi.spyOn(integrationService, "getAzureDevOpsResources").mockImplementation(async (org) => status(org!, org === "Alpha"));
  });

  it("keeps status and cached responses separate for each organization", async () => {
    const client = renderManager();
    expect(await (await row("Alpha")).findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED)).toBeInTheDocument();
    expect(await (await row("Beta")).findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_NOT_INSTALLED)).toBeInTheDocument();
    expect(client.getQueryData(["azure-devops-resources", "Alpha"])).toEqual(status("Alpha", true));
    expect(client.getQueryData(["azure-devops-resources", "Beta"])).toEqual(status("Beta"));
  });

  it("installs only the chosen organization and refreshes its status", async () => {
    const user = userEvent.setup();
    const install = vi.spyOn(integrationService, "reinstallAzureDevOpsWebhook").mockImplementation(async (org) => {
      vi.mocked(integrationService.getAzureDevOpsResources).mockImplementation(async (name) => status(name!, true));
      return { ...status(org!, true), success: true, error: null };
    });
    renderManager();
    const beta = await row("Beta");
    await user.click(await beta.findByRole("button", { name: I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL }));
    expect(install).toHaveBeenCalledExactlyOnceWith("Beta");
    await waitFor(() => expect(beta.getByText(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED)).toBeInTheDocument());
    expect(vi.mocked(integrationService.getAzureDevOpsResources).mock.calls.filter(([name]) => name === "Alpha")).toHaveLength(1);
  });

  it("uninstalls only the selected organization", async () => {
    const user = userEvent.setup();
    const uninstall = vi.spyOn(integrationService, "uninstallAzureDevOpsWebhook").mockResolvedValue({ ...status("Alpha"), success: true, error: null });
    renderManager();
    await user.click(await (await row("Alpha")).findByRole("button", { name: I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL }));
    expect(uninstall).toHaveBeenCalledExactlyOnceWith("Alpha");
  });

  it("keeps another organization usable when one status request fails", async () => {
    vi.mocked(integrationService.getAzureDevOpsResources).mockImplementation(async (org) => {
      if (org === "Alpha") throw new Error("Access denied");
      return status(org!);
    });
    renderManager();
    expect(await (await row("Alpha")).findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_ERROR)).toBeInTheDocument();
    expect(await (await row("Beta")).findByRole("button", { name: I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL })).toBeEnabled();
  });

  it("does not fall back to a configured organization after discovery fails", async () => {
    vi.mocked(integrationService.getAzureDevOpsOrganizations).mockRejectedValue(new Error("Membership unavailable"));
    renderManager();
    expect(await screen.findByText(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_ERROR)).toBeInTheDocument();
    expect(integrationService.getAzureDevOpsResources).not.toHaveBeenCalled();
  });

  it("shows a clear empty state when the account has no organizations", async () => {
    vi.mocked(integrationService.getAzureDevOpsOrganizations).mockResolvedValue({ organizations: [], default_organization: null });
    renderManager();
    expect(await screen.findByText(I18nKey.AZURE_DEVOPS$NO_ORGANIZATIONS)).toBeInTheDocument();
    expect(integrationService.getAzureDevOpsResources).not.toHaveBeenCalled();
  });
});
