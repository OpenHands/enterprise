import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { licenseService } from "#/api/license-service/license-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { SuperAdminLicense } from "#/components/features/super-admin/super-admin-pages";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import translations from "#/i18n/translation.json";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

// The page links Customer Center through <Trans>, which needs a real i18n
// instance.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          LICENSE$GET_FROM_CUSTOMER_CENTER:
            translations.LICENSE$GET_FROM_CUSTOMER_CENTER.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

async function renderLicensePage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await queryClient.prefetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SuperAdminLicense />
    </QueryClientProvider>,
  );
}

describe("SuperAdminLicense", () => {
  beforeEach(() => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    vi.spyOn(licenseService, "getLicense").mockResolvedValue({
      source: null,
      kind: null,
      key_hint: null,
    });
    vi.spyOn(licenseService, "getStatus").mockResolvedValue({
      state: "disabled",
      reason: null,
      offline: false,
      type: null,
      license_ends_at: null,
      expires_at: null,
      last_checked_at: null,
      customer_center_url: "https://customercenter.example.com",
      features: { sso: true },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("links Customer Center", async () => {
    // Act
    await renderLicensePage();

    // Assert
    const link = await screen.findByTestId("customer-center-link");
    expect(link).toHaveTextContent("Customer Center");
    expect(link).toHaveAttribute("href", "https://customercenter.example.com");
  });
});
