import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { licenseService } from "#/api/license-service/license-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import SuperAdminInstallLicense from "#/routes/super-admin-install-license";
import {
  getSuperAdminNuxStep,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";

const LICENSE_KEY = `ohe_lic_AbCd1234${"x".repeat(24)}`;

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
});
