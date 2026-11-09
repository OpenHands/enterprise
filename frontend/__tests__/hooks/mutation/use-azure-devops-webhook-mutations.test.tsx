import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useReinstallAzureDevOpsWebhook } from "#/hooks/mutation/use-reinstall-azure-devops-webhook";
import { useUninstallAzureDevOpsWebhook } from "#/hooks/mutation/use-uninstall-azure-devops-webhook";
import { integrationService } from "#/api/integration-service/integration-service.api";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

vi.mock("#/api/integration-service/integration-service.api", () => ({
  integrationService: {
    getAzureDevOpsResources: vi.fn(),
    reinstallAzureDevOpsWebhook: vi.fn(),
    uninstallAzureDevOpsWebhook: vi.fn(),
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("#/utils/custom-toast-handlers", () => ({
  displaySuccessToast: vi.fn(),
  displayErrorToast: vi.fn(),
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

const azureRejection = (detail: string) =>
  new AxiosError(
    "Request failed with status code 502",
    "ERR_BAD_RESPONSE",
    undefined,
    undefined,
    {
      status: 502,
      statusText: "Bad Gateway",
      data: { detail },
      headers: {},
      config: { headers: new AxiosHeaders() },
    },
  );

describe("Azure DevOps webhook mutations", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  const installed = {
    organization: "example",
    success: true,
    error: null,
    pr_subscription_id: "pr-hook",
    work_item_subscription_id: "work-hook",
    webhook_url: "https://example.test/integration/azure-devops/events",
  };
  const verified = {
    organization: "example",
    can_view_webhooks: true,
    can_manage_webhooks: true,
    webhook_secret_set: true,
    webhook_installed: true,
    pr_webhook_installed: true,
    work_item_webhook_installed: true,
    pr_subscription_id: "pr-hook",
    work_item_subscription_id: "work-hook",
    webhook_url: installed.webhook_url,
    install_recorded: true,
    installed_at: null,
    last_verified_at: null,
    automation_events_enabled: true,
    automation_webhooks_installed: true,
  };

  it("waits for Azure status before showing installation success", async () => {
    vi.mocked(integrationService.reinstallAzureDevOpsWebhook).mockResolvedValue(
      installed,
    );
    let finishVerification!: (value: typeof verified) => void;
    vi.mocked(integrationService.getAzureDevOpsResources).mockReturnValue(
      new Promise((resolve) => {
        finishVerification = resolve;
      }),
    );
    const { result } = renderHook(
      () => useReinstallAzureDevOpsWebhook("example"),
      {
        wrapper: createWrapper(),
      },
    );
    result.current.mutate();
    await waitFor(() =>
      expect(integrationService.getAzureDevOpsResources).toHaveBeenCalledWith(
        "example",
      ),
    );
    expect(result.current.isPending).toBe(true);
    expect(displaySuccessToast).not.toHaveBeenCalled();
    finishVerification(verified);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(displaySuccessToast).toHaveBeenCalledWith(
      "AZURE_DEVOPS$WEBHOOK_INSTALL_SUCCESS",
    );
    expect(displayErrorToast).not.toHaveBeenCalled();
  });

  it.each([
    { webhook_installed: false },
    { automation_webhooks_installed: false },
    { can_view_webhooks: false },
  ])(
    "does not report success when verification is incomplete: %j",
    async (status) => {
      vi.mocked(
        integrationService.reinstallAzureDevOpsWebhook,
      ).mockResolvedValue(installed);
      vi.mocked(integrationService.getAzureDevOpsResources).mockResolvedValue({
        ...verified,
        ...status,
      });
      const { result } = renderHook(
        () => useReinstallAzureDevOpsWebhook("example"),
        {
          wrapper: createWrapper(),
        },
      );
      result.current.mutate();
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(displaySuccessToast).not.toHaveBeenCalled();
      expect(displayErrorToast).toHaveBeenCalledWith(
        "AZURE_DEVOPS$WEBHOOK_INSTALL_UNVERIFIED",
      );
    },
  );

  it("does not require automation hooks when forwarding is disabled", async () => {
    vi.mocked(integrationService.reinstallAzureDevOpsWebhook).mockResolvedValue(
      installed,
    );
    vi.mocked(integrationService.getAzureDevOpsResources).mockResolvedValue({
      ...verified,
      automation_events_enabled: false,
      automation_webhooks_installed: false,
    });
    const { result } = renderHook(
      () => useReinstallAzureDevOpsWebhook("example"),
      {
        wrapper: createWrapper(),
      },
    );
    result.current.mutate();
    await waitFor(() => expect(displaySuccessToast).toHaveBeenCalled());
    expect(displayErrorToast).not.toHaveBeenCalled();
  });

  it("reports a failed status check separately from the accepted installation", async () => {
    vi.mocked(integrationService.reinstallAzureDevOpsWebhook).mockResolvedValue(
      installed,
    );
    vi.mocked(integrationService.getAzureDevOpsResources).mockRejectedValue(
      azureRejection("Azure status unavailable."),
    );
    const { result } = renderHook(
      () => useReinstallAzureDevOpsWebhook("example"),
      {
        wrapper: createWrapper(),
      },
    );
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(displaySuccessToast).not.toHaveBeenCalled();
    expect(displayErrorToast).toHaveBeenCalledWith(
      "AZURE_DEVOPS$WEBHOOK_INSTALL_UNVERIFIED Azure status unavailable.",
    );
  });

  it("shows the server's error detail when install fails", async () => {
    const detail =
      "Azure DevOps rejected the request to install Service Hooks (HTTP 400): Not allowed.";
    vi.mocked(integrationService.reinstallAzureDevOpsWebhook).mockRejectedValue(
      azureRejection(detail),
    );

    const { result } = renderHook(() => useReinstallAzureDevOpsWebhook(), {
      wrapper: createWrapper(),
    });
    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(displayErrorToast).toHaveBeenCalledWith(detail);
  });

  it("shows the server's error detail when uninstall fails", async () => {
    const detail =
      "Azure DevOps rejected the request to uninstall Service Hooks (HTTP 403): Access denied.";
    vi.mocked(integrationService.uninstallAzureDevOpsWebhook).mockRejectedValue(
      azureRejection(detail),
    );

    const { result } = renderHook(() => useUninstallAzureDevOpsWebhook(), {
      wrapper: createWrapper(),
    });
    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(displayErrorToast).toHaveBeenCalledWith(detail);
  });
});
