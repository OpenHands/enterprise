import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getLicenseBannerMessage,
  LicenseBanner,
} from "#/components/features/settings/license-banner";
import type { LicenseStatus } from "#/api/license-service/license-service.api";
import { I18nKey } from "#/i18n/declaration";

const mocks = vi.hoisted(() => ({
  config: { app_mode: "saas" } as { app_mode: string } | undefined,
  status: undefined as LicenseStatus | undefined,
  enabled: undefined as boolean | undefined,
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: mocks.config }),
}));

vi.mock("#/hooks/query/use-license-status", () => ({
  useLicenseStatus: (options?: { enabled?: boolean }) => {
    mocks.enabled = options?.enabled;
    return { data: options?.enabled ? mocks.status : undefined };
  },
}));

function status(overrides: Partial<LicenseStatus>): LicenseStatus {
  return {
    state: "valid",
    reason: null,
    offline: false,
    license_ends_at: null,
    plan: "enterprise",
    subscription_state: "subscription_active_paid",
    trial_ends_at: null,
    overdue_since: null,
    expires_at: "2026-11-15T12:00:00Z",
    last_checked_at: "2026-11-01T12:00:00Z",
    customer_center_url: "https://customercenter.openhands.dev",
    ...overrides,
  };
}

describe("getLicenseBannerMessage", () => {
  it.each([
    ["disabled", status({ state: "disabled" })],
    ["pending", status({ state: "pending" })],
    ["paid", status({})],
    [
      "a trial without an end date",
      status({ subscription_state: "trial_active" }),
    ],
  ])("shows nothing when %s", (_, value) => {
    expect(getLicenseBannerMessage(value)).toBeNull();
  });

  it.each([
    ["revoked", I18nKey.LICENSE$REVOKED],
    ["trial_expired", I18nKey.LICENSE$TRIAL_EXPIRED],
    ["subscription_cancelled", I18nKey.LICENSE$SUBSCRIPTION_CANCELLED],
    ["no_subscription", I18nKey.LICENSE$NO_SUBSCRIPTION],
    ["invalid_license_file", I18nKey.LICENSE$INVALID_FILE],
    ["invalid_key", I18nKey.LICENSE$INVALID_KEY],
    ["a_reason_added_later", I18nKey.LICENSE$INVALID_KEY],
  ])("shows an error for the invalid reason %s", (reason, key) => {
    expect(
      getLicenseBannerMessage(status({ state: "invalid", reason })),
    ).toEqual({ tone: "error", key });
  });

  describe("with an offline license", () => {
    const NOW = new Date("2026-11-01T12:00:00Z");
    const offline = (licenseEndsAt: string) =>
      status({ offline: true, license_ends_at: licenseEndsAt });

    it("shows nothing more than 7 days before the license ends", () => {
      expect(
        getLicenseBannerMessage(offline("2026-11-08T12:00:01Z"), NOW),
      ).toBeNull();
    });

    it("warns 7 days before the license ends", () => {
      expect(
        getLicenseBannerMessage(offline("2026-11-08T12:00:00Z"), NOW),
      ).toEqual({
        tone: "warning",
        key: I18nKey.LICENSE$OFFLINE_ENDS_SOON,
        date: "2026-11-08T12:00:00Z",
      });
    });

    it("warns about the license end over a trial end date", () => {
      expect(
        getLicenseBannerMessage(
          status({
            offline: true,
            license_ends_at: "2026-11-06T12:00:00Z",
            subscription_state: "trial_active",
            trial_ends_at: "2026-11-06T12:00:00Z",
          }),
          NOW,
        )?.key,
      ).toBe(I18nKey.LICENSE$OFFLINE_ENDS_SOON);
    });

    it("shows the date the license expired", () => {
      expect(
        getLicenseBannerMessage(
          status({
            state: "invalid",
            reason: "license_expired",
            offline: true,
            license_ends_at: "2026-10-31T12:00:00Z",
          }),
          NOW,
        ),
      ).toEqual({
        tone: "error",
        key: I18nKey.LICENSE$EXPIRED,
        date: "2026-10-31T12:00:00Z",
      });
    });
  });

  it("shows an error when the key is missing", () => {
    expect(getLicenseBannerMessage(status({ state: "missing_key" }))).toEqual({
      tone: "error",
      key: I18nKey.LICENSE$MISSING_KEY,
    });
  });

  it("warns when the license can't be verified", () => {
    expect(getLicenseBannerMessage(status({ state: "unverified" }))).toEqual({
      tone: "warning",
      key: I18nKey.LICENSE$UNVERIFIED,
    });
  });

  it("shows the trial end date", () => {
    expect(
      getLicenseBannerMessage(
        status({
          subscription_state: "trial_extended",
          trial_ends_at: "2026-11-11T12:00:00Z",
        }),
      ),
    ).toEqual({
      tone: "info",
      key: I18nKey.LICENSE$TRIAL_ENDS,
      date: "2026-11-11T12:00:00Z",
    });
  });

  it.each([
    ["subscription_active_overdue", "warning"],
    ["subscription_inactive_overdue", "error"],
  ])("shows an overdue payment for %s as %s", (subscriptionState, tone) => {
    expect(
      getLicenseBannerMessage(
        status({
          subscription_state: subscriptionState,
          overdue_since: "2026-10-20T00:00:00Z",
        }),
      ),
    ).toEqual({
      tone,
      key: I18nKey.LICENSE$PAYMENT_OVERDUE,
      date: "2026-10-20T00:00:00Z",
    });
  });
});

describe("LicenseBanner", () => {
  beforeEach(() => {
    mocks.config = { app_mode: "saas" };
    mocks.status = undefined;
    mocks.enabled = undefined;
  });

  it("renders the message and a link to Customer Center", () => {
    mocks.status = status({ state: "invalid", reason: "revoked" });

    render(<LicenseBanner />);

    const banner = screen.getByTestId("license-banner");
    expect(banner).toHaveAttribute("data-tone", "error");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent(I18nKey.LICENSE$REVOKED);
    expect(
      screen.getByRole("link", { name: I18nKey.LICENSE$OPEN_CUSTOMER_CENTER }),
    ).toHaveAttribute("href", "https://customercenter.openhands.dev");
  });

  it("renders nothing for a paid license", () => {
    mocks.status = status({});

    render(<LicenseBanner />);

    expect(screen.queryByTestId("license-banner")).not.toBeInTheDocument();
  });

  it("does not fetch the status outside SaaS mode", () => {
    mocks.config = { app_mode: "oss" };
    mocks.status = status({ state: "missing_key" });

    render(<LicenseBanner />);

    expect(mocks.enabled).toBe(false);
    expect(screen.queryByTestId("license-banner")).not.toBeInTheDocument();
  });
});
