import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useReinstallAzureDevOpsWebhook } from "#/hooks/mutation/use-reinstall-azure-devops-webhook";
import { useUninstallAzureDevOpsWebhook } from "#/hooks/mutation/use-uninstall-azure-devops-webhook";
import { integrationService } from "#/api/integration-service/integration-service.api";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

vi.mock("#/api/integration-service/integration-service.api", () => ({
  integrationService: {
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
    vi.clearAllMocks();
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
