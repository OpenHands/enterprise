import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { useConfig } from "#/hooks/query/use-config";
import { useLlmApiKey } from "#/hooks/query/use-llm-api-key";
import { ApiKeysManager } from "#/components/features/settings/api-keys-manager";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const mockUseConfig = vi.mocked(useConfig);
const mockUseLlmApiKey = vi.mocked(useLlmApiKey);

// Mock the react-i18next
vi.mock("react-i18next", async () => {
  const actual = await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string) => key,
    }),
    Trans: ({ i18nKey, components }: { i18nKey: string; components: Record<string, React.ReactNode> }) => {
      // Simplified Trans component that renders the link
      if (i18nKey === "SETTINGS$API_KEYS_DESCRIPTION") {
        return (
          <span>
            API keys allow you to authenticate with the OpenHands API programmatically.
            Keep your API keys secure; anyone with your API key can access your account.
            For more information on how to use the API, see our {components.a}
          </span>
        );
      }
      return <span>{i18nKey}</span>;
    },
  };
});

// Mock the API keys hook
vi.mock("#/hooks/query/use-api-keys", () => ({
  useApiKeys: () => ({
    data: [],
    isLoading: false,
    error: null,
  }),
}));

// Mock the config hook so we can control feature flags per-test.
vi.mock("#/hooks/query/use-config", () => ({
  useConfig: vi.fn(),
}));

// Mock the LLM API key hook so we can control the payment-required state.
vi.mock("#/hooks/query/use-llm-api-key", () => ({
  useLlmApiKey: vi.fn(),
}));

// Mock the refresh mutation hook.
vi.mock("#/hooks/mutation/use-refresh-llm-api-key", () => ({
  useRefreshLlmApiKey: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

describe("ApiKeysManager", () => {
  beforeEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: "test-org-id" });
    mockUseConfig.mockReturnValue({
      data: {
        app_mode: "oss",
        feature_flags: {
          enable_billing: false,
          enable_byor_export: false,
        } as never,
      },
    } as never);
    mockUseLlmApiKey.mockReturnValue({
      data: { key: "sk-test-key" },
      error: null,
      isLoading: false,
      isPaymentRequired: false,
    } as never);
  });

  const renderComponent = () => {
    const queryClient = new QueryClient();
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ApiKeysManager />
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  it("should render the API documentation link", () => {
    renderComponent();

    // Find the link to the API documentation
    const link = screen.getByRole("link");
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "https://docs.all-hands.dev/usage/cloud/cloud-api");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows the disabled message when billing and byor export are both off", () => {
    renderComponent();

    expect(
      screen.getByText("SETTINGS$LLM_API_KEY_DISABLED_MESSAGE"),
    ).toBeInTheDocument();
    // The buy-credits paywall should not appear.
    expect(
      screen.queryByText("SETTINGS$LLM_API_KEY_PAYWALL_MESSAGE"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("SETTINGS$LLM_API_KEY_BUY_NOW"),
    ).not.toBeInTheDocument();
  });

  it("shows the buy-credits paywall when billing is on but payment is required", () => {
    mockUseConfig.mockReturnValue({
      data: {
        app_mode: "saas",
        feature_flags: { enable_billing: true, enable_byor_export: false },
      } as never,
    } as never);
    mockUseLlmApiKey.mockReturnValue({
      data: undefined,
      error: new Error("402"),
      isLoading: false,
      isPaymentRequired: true,
    } as never);

    renderComponent();

    expect(
      screen.getByText("SETTINGS$LLM_API_KEY_PAYWALL_MESSAGE"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SETTINGS$LLM_API_KEY_BUY_NOW"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("SETTINGS$LLM_API_KEY_DISABLED_MESSAGE"),
    ).not.toBeInTheDocument();
  });

  it("shows the LLM key manager when byor export is enabled without billing", () => {
    mockUseConfig.mockReturnValue({
      data: {
        app_mode: "oss",
        feature_flags: { enable_billing: false, enable_byor_export: true },
      } as never,
    } as never);
    mockUseLlmApiKey.mockReturnValue({
      data: { key: "sk-byor-key" },
      error: null,
      isLoading: false,
      isPaymentRequired: false,
    } as never);

    renderComponent();

    // The refresh button is rendered by the active key manager.
    expect(
      screen.getByText("SETTINGS$REFRESH_LLM_API_KEY"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("SETTINGS$LLM_API_KEY_DISABLED_MESSAGE"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("SETTINGS$LLM_API_KEY_PAYWALL_MESSAGE"),
    ).not.toBeInTheDocument();
  });
});
