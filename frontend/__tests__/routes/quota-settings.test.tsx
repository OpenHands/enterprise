import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "test-utils";
import QuotaSettingsScreen from "#/routes/quota-settings";
import OptionService from "#/api/option-service/option-service.api";
import {
  quotaService,
  type QuotaStatus,
} from "#/api/quota-service/quota-service.api";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const buildQuotaStatus = (
  overrides: Partial<QuotaStatus> = {},
): QuotaStatus => ({
  daily_limit: 20,
  used_today: 3,
  remaining: 17,
  reset_at: new Date(Date.now() + 3_600_000).toISOString(),
  work_email: null,
  work_email_verified: false,
  latest_request_status: null,
  latest_request_requested_limit: null,
  ...overrides,
});

describe("QuotaSettingsScreen", () => {
  beforeEach(() => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hides the reset countdown when the quota is unlimited", async () => {
    // Arrange
    vi.spyOn(quotaService, "getStatus").mockResolvedValue(
      buildQuotaStatus({ daily_limit: null, remaining: null }),
    );

    // Act
    renderWithProviders(<QuotaSettingsScreen />);
    await screen.findByTestId("quota-status-card");

    // Assert
    expect(
      screen.queryByTestId("quota-reset-countdown"),
    ).not.toBeInTheDocument();
  });

  it("shows the reset countdown when a daily limit is set", async () => {
    // Arrange
    vi.spyOn(quotaService, "getStatus").mockResolvedValue(buildQuotaStatus());

    // Act
    renderWithProviders(<QuotaSettingsScreen />);
    await screen.findByTestId("quota-status-card");

    // Assert
    expect(screen.getByTestId("quota-reset-countdown")).toBeInTheDocument();
  });
});
