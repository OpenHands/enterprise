import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { createRoutesStub } from "react-router";
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
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import translations from "#/i18n/translation.json";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import SuperAdminInstallLicense from "#/routes/super-admin-install-license";
import {
  getSuperAdminNuxStep,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";

const LICENSE_KEY = `ohe_lic_AbCd1234${"x".repeat(24)}`;

// The step's text links Customer Center through <Trans>, which needs a real
// i18n instance.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          SA_NUX$LICENSE_BODY: translations.SA_NUX$LICENSE_BODY.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

async function renderLicenseStep() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The install layout's loader caches the config before this step renders.
  await queryClient.prefetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
  });
  const RouterStub = createRoutesStub([
    { path: "/install/license", Component: SuperAdminInstallLicense },
    {
      path: "/install/company",
      Component: () => <div data-testid="install-company-step" />,
    },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={["/install/license"]} />
    </QueryClientProvider>,
  );
  await screen.findByTestId("super-admin-install-license");
}

describe("super admin install license step", () => {
  beforeEach(() => {
    setSuperAdminNuxStep("license");
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );
    vi.spyOn(licenseService, "getLicense").mockResolvedValue({
      source: null,
      kind: null,
      key_hint: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("links Customer Center in the step's text", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
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

    // Act
    await renderLicenseStep();

    // Assert
    const link = await screen.findByTestId("customer-center-link");
    expect(link).toHaveTextContent("Customer Center");
    expect(link).toHaveAttribute("href", "https://customercenter.example.com");
  });

  it("opens the company step without a license when the admin skips", async () => {
    // Arrange
    const user = userEvent.setup();
    const saveLicense = vi.spyOn(licenseService, "saveLicense");
    await renderLicenseStep();

    // Act
    await user.click(screen.getByTestId("sa-nux-license-continue"));

    // Assert
    expect(await screen.findByTestId("install-company-step")).toBeVisible();
    expect(saveLicense).not.toHaveBeenCalled();
    expect(getSuperAdminNuxStep()).toBe("company");
  });

  it("saves a license key, then opens the company step", async () => {
    // Arrange
    const user = userEvent.setup();
    const saveLicense = vi
      .spyOn(licenseService, "saveLicense")
      .mockResolvedValue({
        source: "saved",
        kind: "key",
        key_hint: "ohe_lic_AbCd1234",
      });
    await renderLicenseStep();

    // Act
    await user.type(
      await screen.findByTestId("license-key-input"),
      LICENSE_KEY,
    );
    await user.click(screen.getByTestId("license-key-save"));

    // Assert
    expect(await screen.findByTestId("install-company-step")).toBeVisible();
    expect(saveLicense).toHaveBeenCalledWith({ license_key: LICENSE_KEY });
    expect(getSuperAdminNuxStep()).toBe("company");
  });

  it("stays on the license step when the saved license is invalid", async () => {
    const user = userEvent.setup();
    const savedKey = {
      source: "saved",
      kind: "key",
      key_hint: "ohe_lic_AbCd1234",
    } as const;
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    const getLicense = vi.spyOn(licenseService, "getLicense");
    const saveLicense = vi
      .spyOn(licenseService, "saveLicense")
      .mockImplementation(async () => {
        getLicense.mockResolvedValue(savedKey);
        return savedKey;
      });
    vi.spyOn(licenseService, "getStatus").mockResolvedValue({
      state: "invalid",
      reason: "invalid_key",
      offline: false,
      type: null,
      license_ends_at: null,
      expires_at: null,
      last_checked_at: null,
      customer_center_url: "https://customercenter.example.com",
      features: { sso: false },
    });
    await renderLicenseStep();

    await user.type(
      await screen.findByTestId("license-key-input"),
      LICENSE_KEY,
    );
    await user.click(screen.getByTestId("license-key-save"));

    await waitFor(() => expect(saveLicense).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("license-status")).toHaveTextContent(
      "LICENSE$INVALID_KEY",
    );
    expect(
      screen.queryByTestId("install-company-step"),
    ).not.toBeInTheDocument();
    expect(getSuperAdminNuxStep()).toBe("license");
    expect(screen.getByTestId("sa-nux-license-continue")).toHaveTextContent(
      "SA_NUX$LICENSE_SKIP",
    );
    await user.click(screen.getByTestId("sa-nux-license-continue"));
    expect(await screen.findByTestId("install-company-step")).toBeVisible();
    expect(getSuperAdminNuxStep()).toBe("company");
  });
});
