import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  licenseService,
  type ConfiguredLicense,
  type LicenseStatus,
} from "#/api/license-service/license-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { LicenseSettings } from "#/components/features/license/license-settings";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const LICENSE_KEY = `ohe_lic_AbCd1234${"x".repeat(24)}`;

const NO_LICENSE: ConfiguredLicense = {
  source: null,
  kind: null,
  key_hint: null,
};

const SAVED_KEY: ConfiguredLicense = {
  source: "saved",
  kind: "key",
  key_hint: "ohe_lic_AbCd1234",
};

const SAVED_OFFLINE: ConfiguredLicense = {
  source: "saved",
  kind: "offline",
  key_hint: null,
};

const ENFORCEMENT_OFF: LicenseStatus = {
  state: "disabled",
  reason: null,
  offline: false,
  type: null,
  license_ends_at: null,
  expires_at: null,
  last_checked_at: null,
  customer_center_url: "https://customercenter.openhands.dev",
  features: { sso: true },
};

async function renderLicenseSettings(onSaved?: () => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The pages that show the license render once the config has loaded.
  await queryClient.prefetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
  });
  render(
    <QueryClientProvider client={queryClient}>
      <LicenseSettings onSaved={onSaved} />
    </QueryClientProvider>,
  );
}

describe("LicenseSettings", () => {
  beforeEach(() => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    vi.spyOn(licenseService, "getStatus").mockResolvedValue(ENFORCEMENT_OFF);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says when no license is set", async () => {
    // Arrange
    vi.spyOn(licenseService, "getLicense").mockResolvedValue(NO_LICENSE);

    // Act
    await renderLicenseSettings();

    // Assert
    expect(await screen.findByTestId("license-current")).toHaveTextContent(
      "LICENSE$NONE_SET",
    );
  });

  it("saves a typed license key and shows it as the current license", async () => {
    // Arrange
    const user = userEvent.setup();
    const onSaved = vi.fn();
    vi.spyOn(licenseService, "getLicense").mockResolvedValue(NO_LICENSE);
    const saveLicense = vi
      .spyOn(licenseService, "saveLicense")
      .mockResolvedValue(SAVED_KEY);
    await renderLicenseSettings(onSaved);

    // Act
    await user.type(
      await screen.findByTestId("license-key-input"),
      ` ${LICENSE_KEY} `,
    );
    await user.click(screen.getByTestId("license-key-save"));

    // Assert
    await waitFor(() =>
      expect(saveLicense).toHaveBeenCalledWith({ license_key: LICENSE_KEY }),
    );
    expect(await screen.findByTestId("license-key-hint")).toHaveTextContent(
      "ohe_lic_AbCd1234",
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  });

  it("does not call onSaved when the server finds the saved license invalid", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    vi.spyOn(licenseService, "getLicense").mockResolvedValue(NO_LICENSE);
    const saveLicense = vi
      .spyOn(licenseService, "saveLicense")
      .mockResolvedValue(SAVED_KEY);
    vi.spyOn(licenseService, "getStatus").mockResolvedValue({
      ...ENFORCEMENT_OFF,
      state: "invalid",
      reason: "invalid_key",
    });
    await renderLicenseSettings(onSaved);

    await user.type(
      await screen.findByTestId("license-key-input"),
      LICENSE_KEY,
    );
    await user.click(screen.getByTestId("license-key-save"));

    await waitFor(() => expect(saveLicense).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("license-status")).toHaveTextContent(
      "LICENSE$INVALID_KEY",
    );
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("saves the contents of an uploaded offline license file", async () => {
    // Arrange
    const user = userEvent.setup();
    const contents = JSON.stringify({ key_id: "prod-1", payload: "{}" });
    const file = new File([contents], "openhands-license.json", {
      type: "application/json",
    });
    // jsdom's File cannot read itself as text.
    file.text = async () => contents;
    vi.spyOn(licenseService, "getLicense").mockResolvedValue(SAVED_KEY);
    const saveLicense = vi
      .spyOn(licenseService, "saveLicense")
      .mockResolvedValue(SAVED_OFFLINE);
    await renderLicenseSettings();

    // Act
    await user.upload(await screen.findByTestId("license-offline-input"), file);

    // Assert
    await waitFor(() =>
      expect(saveLicense).toHaveBeenCalledWith({ offline_license: contents }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("license-current")).toHaveTextContent(
        "SUPER_ADMIN$INSTANCE_OFFLINE_LICENSE",
      ),
    );
  });

  it("cannot be changed when the license is set in the environment", async () => {
    // Arrange
    vi.spyOn(licenseService, "getLicense").mockResolvedValue({
      ...SAVED_KEY,
      source: "environment",
    });

    // Act
    await renderLicenseSettings();

    // Assert
    expect(
      await screen.findByTestId("license-from-environment"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("license-key-hint")).toHaveTextContent(
      "ohe_lic_AbCd1234",
    );
    expect(screen.queryByTestId("license-key-input")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("license-offline-input"),
    ).not.toBeInTheDocument();
  });

  it("reports a saved license that is not valid", async () => {
    // Arrange
    vi.spyOn(licenseService, "getLicense").mockResolvedValue(SAVED_KEY);
    vi.spyOn(licenseService, "getStatus").mockResolvedValue({
      ...ENFORCEMENT_OFF,
      state: "invalid",
      reason: "invalid_key",
    });

    // Act
    await renderLicenseSettings();

    // Assert
    expect(await screen.findByTestId("license-status")).toHaveTextContent(
      "LICENSE$INVALID_KEY",
    );
  });
});
