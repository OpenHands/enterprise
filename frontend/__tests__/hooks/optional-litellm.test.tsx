import { useSettings } from "#/hooks/query/use-settings";
import { MOCK_DEFAULT_USER_SETTINGS } from "#/mocks/handlers";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { deferred } from "../helpers/native-fixtures";
import { useOrgUsageStats } from "#/hooks/query/use-org-usage-stats";
import { useOrgUserUsage } from "#/hooks/query/use-org-user-usage";
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "#/mocks/node";
import { WebClientConfig } from "#/api/option-service/option.types";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { useBalance } from "#/hooks/query/use-balance";
import { useLlmApiKey } from "#/hooks/query/use-llm-api-key";
import { useOrganizationPaymentInfo } from "#/hooks/query/use-organization-payment-info";
import { useBudgetSettings } from "#/hooks/query/use-budget-settings";
import { useBudgetMutations } from "#/hooks/mutation/use-budget-mutations";
import { useRefreshLlmApiKey } from "#/hooks/mutation/use-refresh-llm-api-key";
import { useCreateBillingSession } from "#/hooks/mutation/use-create-billing-session";
import { useCreateStripeCheckoutSession } from "#/hooks/mutation/stripe/use-create-stripe-checkout-session";
import { useApiKeys } from "#/hooks/query/use-api-keys";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { useSearchProviders } from "#/hooks/query/use-search-providers";
import { useProviderModels } from "#/hooks/query/use-provider-models";
import BudgetsPage from "#/routes/budgets";
import { ApiKeysManager } from "#/components/features/settings/api-keys-manager";
import { UsersTab } from "#/components/features/admin-dashboard/usage-dashboard-tabs";
import { PaymentForm } from "#/components/features/payment/payment-form";
import { isBillingHidden } from "#/utils/org/billing-visibility";

vi.mock("#/utils/custom-toast-handlers", () => ({
  displayErrorToast: vi.fn(),
  displaySuccessToast: vi.fn(),
}));

const config = (enabled?: boolean): WebClientConfig => {
  const defaults = createMockWebClientConfig({ app_mode: "saas" });
  return { ...defaults, feature_flags: { ...defaults.feature_flags, enable_litellm: enabled, enable_billing: true, enable_byor_export: true } };
};

const gatewayCalls = vi.fn();
const appKeyCalls = vi.fn();
const setup = (initialConfig?: WebClientConfig): { client: QueryClient; wrapper: ({ children }: { children: React.ReactNode; }) => React.JSX.Element; } => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  if (initialConfig)
    client.setQueryData(QUERY_KEYS.WEB_CLIENT_CONFIG, initialConfig);
  const wrapper = ({ children }: { children: React.ReactNode }): React.JSX.Element => (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  return { client, wrapper };
};
function useGatewayHooks(): { capability: ReturnType<typeof useLiteLlmIntegration>; balance: ReturnType<typeof useBalance>; key: ReturnType<typeof useLlmApiKey>; payment: ReturnType<typeof useOrganizationPaymentInfo>; budget: ReturnType<typeof useBudgetSettings>; budgets: ReturnType<typeof useBudgetMutations>; refresh: ReturnType<typeof useRefreshLlmApiKey>; billing: ReturnType<typeof useCreateBillingSession>; checkout: ReturnType<typeof useCreateStripeCheckoutSession>; appKeys: ReturnType<typeof useApiKeys> } {
  return {
    capability: useLiteLlmIntegration(),
    balance: useBalance(),
    key: useLlmApiKey(),
    payment: useOrganizationPaymentInfo(),
    budget: useBudgetSettings({ usersPage: 1 }),
    budgets: useBudgetMutations(),
    refresh: useRefreshLlmApiKey(),
    billing: useCreateBillingSession(),
    checkout: useCreateStripeCheckoutSession(),
    appKeys: useApiKeys(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useSelectedOrganizationStore.setState({ organizationId: "org-1" });
  server.use(
    http.all(
      /\/api\/(billing\/|keys\/llm\/|organizations\/org-1\/(budgets|payment))/,
      ({ request }) => {
        gatewayCalls(request.method, new URL(request.url).pathname);
        return HttpResponse.json({
          credits: "42",
          key: "gateway-secret",
          users: [],
          thresholds: [],
        });
      },
    ),
    http.get("/api/keys", () => {
      appKeyCalls();
      return HttpResponse.json([]);
    }),
  );
});

describe("optional LiteLLM gateway capabilities", () => {
  it.each([false, "loading"] as const)(
    "does not fetch, refetch, mutate, or expose gateway cache with %s config",
    async (state) => {
      const { promise: pendingConfig, resolve: resolveConfig } = deferred<WebClientConfig>();
      server.use(
        http.get("/api/v1/web-client/config", async () =>
          HttpResponse.json(await pendingConfig),
        ),
      );
      const { client, wrapper } = setup(
        state === false ? config(false) : undefined,
      );
      client.setQueryData(["user", "balance"], "999");
      client.setQueryData(["llm-api-key"], { key: "cached-gateway-secret" });
      client.setQueryData(["organizations", "org-1", "payment"], {
        cardNumber: "1234",
      });
      client.setQueryData(
        ["organizations", "budgets", "org-1", 1, "", undefined, undefined],
        { enabled: true },
      );
      const { result } = renderHook(useGatewayHooks, { wrapper });
      expect(result.current.capability.enabled).toBe(false);
      expect(result.current.balance.data).toBeUndefined();
      expect(result.current.key.data).toBeUndefined();
      expect(result.current.payment.data).toBeUndefined();
      expect(result.current.budget.data).toBeUndefined();
      await act(async () => {
        await client.invalidateQueries({
          predicate: (query) => query.queryKey[0] !== "web-client-config",
        });
        focusManager.setFocused(false);
        focusManager.setFocused(true);
        await result.current.balance.refetch();
        await result.current.payment.refetch();
        await result.current.budget.refetch();
        for (const run of [
          () => result.current.refresh.mutateAsync(),
          () => result.current.billing.mutateAsync(),
          () => result.current.checkout.mutateAsync({ amount: 20 }),
          () =>
            result.current.budgets.updateBudgets.mutateAsync({ enabled: true }),
          () =>
            result.current.budgets.upsertOverride.mutateAsync({
              userId: "member",
              payload: { is_disabled: true },
            }),
          () => result.current.budgets.deleteOverride.mutateAsync("member"),
        ])
          await expect(run()).rejects.toThrow(
            "LiteLLM integration is disabled",
          );
        resolveConfig(config(false));
      });
      await waitFor(() => expect(result.current.appKeys.isSuccess).toBe(true));
      expect(appKeyCalls).toHaveBeenCalled();
      expect(gatewayCalls).not.toHaveBeenCalled();
      client.clear();
    },
  );

  it.each([true, undefined])(
    "keeps loaded enabled/old-server behavior (%s)",
    async (enabled) => {
      const { client, wrapper } = setup(config(enabled));
      const { result } = renderHook(useGatewayHooks, { wrapper });
      await waitFor(() => expect(result.current.budget.isSuccess).toBe(true));
      await waitFor(() =>
        expect(result.current.key.data?.key).toBe("gateway-secret"),
      );
      expect(result.current.capability.enabled).toBe(true);
      expect(gatewayCalls).toHaveBeenCalledWith("GET", "/api/billing/credits");
      expect(gatewayCalls).toHaveBeenCalledWith("GET", "/api/keys/llm/byor");
      client.clear();
    },
  );

  it("blocks a stale manual refetch immediately when LiteLLM turns off", async () => {
    const { client, wrapper } = setup(config(true));
    const { result } = renderHook(useBalance, { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const staleRefetch = result.current.refetch;
    gatewayCalls.mockClear();
    await act(async () => {
      client.setQueryData(QUERY_KEYS.WEB_CLIENT_CONFIG, config(false));
      await staleRefetch();
    });
    expect(gatewayCalls).not.toHaveBeenCalled();
    await waitFor(() => expect(result.current.data).toBeUndefined());
    client.clear();
  });

  it("keeps raw settings cached while capability changes their displayed defaults", async () => {
    const raw = { ...MOCK_DEFAULT_USER_SETTINGS, llm_model: "", agent_settings: undefined };
    const fetchSettings = vi.fn(() => HttpResponse.json(raw));
    server.use(http.get("/api/v1/settings", fetchSettings));
    const { client, wrapper } = setup(config(true));
    const { result } = renderHook(useSettings, { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.llm_model).toMatch(/^openhands\//);
    const resourceKey = ["settings", "personal", "org-1"];
    expect(client.getQueryData(resourceKey)).toEqual(raw);
    await act(async () => { client.setQueryData(QUERY_KEYS.WEB_CLIENT_CONFIG, config(false)); });
    await waitFor(() => expect(result.current.data?.llm_model).toBe(""));
    expect(client.getQueryData(resourceKey)).toEqual(raw);
    await act(async () => { client.setQueryData(QUERY_KEYS.WEB_CLIENT_CONFIG, config(true)); });
    await waitFor(() => expect(result.current.data?.llm_model).toMatch(/^openhands\//));
    expect(fetchSettings).toHaveBeenCalledOnce();
    client.clear();
  });

  it("rejects a stale mutation immediately after the canonical flag turns off", async () => {
    const { client, wrapper } = setup(config(true));
    const { result } = renderHook(() => useRefreshLlmApiKey(), { wrapper });
    const oldMutation = result.current.mutateAsync;
    await act(async () => {
      client.setQueryData(QUERY_KEYS.WEB_CLIENT_CONFIG, config(false));
      await expect(oldMutation()).rejects.toThrow(
        "LiteLLM integration is disabled",
      );
    });
    expect(gatewayCalls).not.toHaveBeenCalled();
    client.clear();
  });

  it("gates direct Budgets/payment mounts and hides cached managed export while app keys remain available", async () => {
    const { client, wrapper } = setup(config(false));
    client.setQueryData(["llm-api-key"], { key: "cached-gateway-secret" });
    render(
      <>
        <BudgetsPage />
        <PaymentForm />
        <ApiKeysManager />
      </>,
      { wrapper },
    );
    expect(screen.getByText("BUDGETS$LITELLM_REQUIRED")).toBeInTheDocument();
    expect(screen.queryByTestId("billing-settings")).not.toBeInTheDocument();
    expect(screen.queryByText("cached-gateway-secret")).not.toBeInTheDocument();
    expect(screen.getByText("SETTINGS$OPENHANDS_API_KEYS")).toBeInTheDocument();
    await waitFor(() => expect(appKeyCalls).toHaveBeenCalled());
    expect(gatewayCalls).not.toHaveBeenCalled();
    expect(isBillingHidden(config(false), true)).toBe(true);
    client.clear();
  });

  it("continues fetching application usage statistics with LiteLLM disabled", async () => {
    const usageCalls = vi.fn();
    server.use(
      http.get(
        /\/api\/organizations\/org-1\/conversations\/(usage-stats|user-usage)/,
        () => {
          usageCalls();
          return HttpResponse.json({ items: [], estimated_spend: 12 });
        },
      ),
    );
    const { client, wrapper } = setup(config(false));
    const { result } = renderHook(
      () => ({ usage: useOrgUsageStats(), users: useOrgUserUsage() }),
      { wrapper },
    );
    await waitFor(() =>
      expect(
        result.current.usage.isSuccess && result.current.users.isSuccess,
      ).toBe(true),
    );
    expect(usageCalls).toHaveBeenCalledTimes(2);
    expect(gatewayCalls).not.toHaveBeenCalled();
    client.clear();
  });

  it("preserves usage metrics while hiding gateway budget enforcement", () => {
    const { client, wrapper } = setup(config(false));
    render(
      <UsersTab
        userUsageLoading={false}
        userUsage={{
          items: [
            {
              user_id: "u",
              user_email: "u@example.com",
              conversation_count: 3,
              spend_mtd: 12,
              spend_ytd: 24,
              spend_lifetime: 48,
              budget_monthly_limit: 100,
              budget_is_disabled: false,
            },
          ],
        }}
      />,
      { wrapper },
    );
    expect(screen.getByText("Spend MTD")).toBeInTheDocument();
    expect(screen.getByText("$12.00")).toBeInTheDocument();
    expect(screen.queryByText("Budget")).not.toBeInTheDocument();
    client.clear();
  });

  it("filters cached managed catalog entries without dropping native providers", async () => {
    const { client, wrapper } = setup(config(false));
    client.setQueryData(
      ["config", "providers"],
      [{ name: "openhands" }, { name: "litellm_proxy" }, { name: "openai" }],
    );
    client.setQueryData(
      ["config", "models", "openhands"],
      [{ provider: "openhands", name: "old-model" }],
    );
    const { result } = renderHook(
      () => ({
        providers: useSearchProviders(),
        models: useProviderModels("openhands"),
      }),
      { wrapper },
    );
    expect(result.current.providers.data).toEqual([{ name: "openai" }]);
    expect(result.current.models.data).toEqual([]);
    client.clear();
  });
});
