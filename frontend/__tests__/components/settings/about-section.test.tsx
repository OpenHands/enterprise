import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { AboutSection } from "#/components/features/settings/app-settings/about-section";
import OptionService from "#/api/option-service/option-service.api";
import { I18nKey } from "#/i18n/declaration";
import { useConfig } from "#/hooks/query/use-config";
import {
  licenseService,
  LicenseStatus,
} from "#/api/license-service/license-service.api";

vi.mock("#/api/option-service/option-service.api", () => ({
  default: {
    getServerInfo: vi.fn(),
  },
}));

vi.mock("#/hooks/query/use-config");

vi.mock("#/api/license-service/license-service.api", () => ({
  licenseService: {
    getStatus: vi.fn(),
  },
}));

const renderAboutSection = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AboutSection />
    </QueryClientProvider>,
  );
};

const mockClipboard = (writeText: ReturnType<typeof vi.fn>) => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
};

const mockServerInfoDefault = () =>
  vi.mocked(OptionService.getServerInfo).mockResolvedValue({
    app_version: "1.92.0",
    sdk_version: "1.62.0",
    enterprise_release: null,
  });

// Pure OSS install by default: the license section must not even query
// /api/license/status, matching the existing LicenseBanner gating.
const mockConfig = (appMode: "oss" | "saas" = "oss") => {
  vi.mocked(useConfig).mockReturnValue({
    data: { app_mode: appMode },
  } as unknown as ReturnType<typeof useConfig>);
};

const mockLicenseStatus = (status: LicenseStatus) =>
  vi.mocked(licenseService.getStatus).mockResolvedValue(status);

beforeEach(() => {
  mockConfig("oss");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AboutSection", () => {
  it("renders the server and SDK component versions from /server_info", async () => {
    mockServerInfoDefault();

    renderAboutSection();

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-server-version"),
      ).toHaveTextContent("1.92.0"),
    );
    expect(screen.getByTestId("about-section-sdk-version")).toHaveTextContent(
      "1.62.0",
    );
  });

  it("shows the customer-facing enterprise release as primary when present", async () => {
    vi.mocked(OptionService.getServerInfo).mockResolvedValue({
      app_version: "1.94.1",
      sdk_version: "1.62.0",
      enterprise_release: "0.110.0",
    });

    renderAboutSection();

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-enterprise-release"),
      ).toHaveTextContent("0.110.0"),
    );
  });

  it("honestly shows the unavailable label for the enterprise release when packaging hasn't set it, without inferring it from app_version", async () => {
    vi.mocked(OptionService.getServerInfo).mockResolvedValue({
      app_version: "1.94.1",
      sdk_version: "1.62.0",
      enterprise_release: null,
    });

    renderAboutSection();

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-enterprise-release"),
      ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_VALUE_UNAVAILABLE),
    );
    expect(
      screen.getByTestId("about-section-enterprise-release"),
    ).not.toHaveTextContent("1.94.1");
  });

  it("shows an unavailable message when the request fails (retried once, per useServerInfo)", async () => {
    vi.mocked(OptionService.getServerInfo).mockRejectedValue(
      new Error("Network Error"),
    );

    renderAboutSection();

    await waitFor(
      () =>
        expect(screen.getByTestId("about-section-error")).toBeInTheDocument(),
      { timeout: 5000 },
    );
    expect(
      screen.queryByTestId("about-section-server-version"),
    ).not.toBeInTheDocument();
  });

  it("copies only the allowlisted version fields, never host/resource metadata", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    mockClipboard(writeText);
    mockServerInfoDefault();

    renderAboutSection();

    const copyButton = await screen.findByTestId("about-section-copy-button");
    await user.click(copyButton);

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copiedText = writeText.mock.calls[0][0] as string;
    expect(copiedText).toContain("1.92.0");
    expect(copiedText).toContain("1.62.0");
    expect(copiedText).not.toMatch(/https?:\/\//);
    expect(copiedText).not.toMatch(/uptime|resources|idle_time/i);
  });

  it("does not mark itself 'copied' when the clipboard write is rejected", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    mockClipboard(writeText);
    mockServerInfoDefault();

    renderAboutSection();

    const copyButton = await screen.findByTestId("about-section-copy-button");
    await user.click(copyButton);

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    // A rejected clipboard write must not crash the component or flip it
    // into the "copied" state; it stays on the default copy label.
    expect(copyButton).toHaveTextContent(I18nKey.SETTINGS$ABOUT_COPY_BUTTON);
  });

  it("falls back to the unavailable label per-field when a value is missing (older server)", async () => {
    vi.mocked(OptionService.getServerInfo).mockResolvedValue({
      app_version: "1.92.0",
      sdk_version: "",
      enterprise_release: null,
    });

    renderAboutSection();

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-server-version"),
      ).toHaveTextContent("1.92.0"),
    );
    expect(screen.getByTestId("about-section-sdk-version")).toHaveTextContent(
      I18nKey.SETTINGS$ABOUT_VALUE_UNAVAILABLE,
    );
  });

  describe("license section (SaaS / self-hosted enterprise only)", () => {
    it("does not render or query license status on the pure OSS server", async () => {
      mockConfig("oss");
      mockServerInfoDefault();

      renderAboutSection();

      await waitFor(() =>
        expect(
          screen.getByTestId("about-section-server-version"),
        ).toBeInTheDocument(),
      );
      expect(
        screen.queryByTestId("about-section-license-status"),
      ).not.toBeInTheDocument();
      expect(licenseService.getStatus).not.toHaveBeenCalled();
    });

    it("shows the existing normalized status and type when a license is present", async () => {
      mockConfig("saas");
      mockServerInfoDefault();
      mockLicenseStatus({
        state: "valid",
        reason: null,
        offline: false,
        type: "trial",
        license_ends_at: null,
        expires_at: null,
        last_checked_at: null,
        customer_center_url: "https://customer.example.com",
        features: { sso: true },
      });

      renderAboutSection();

      await waitFor(() =>
        expect(
          screen.getByTestId("about-section-license-status"),
        ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_LICENSE_STATE_VALID),
      );
      expect(
        screen.getByTestId("about-section-license-type"),
      ).toHaveTextContent("trial");
      // The customer_center_url and any key hint are never rendered here.
      expect(screen.queryByText(/customer\.example\.com/)).toBeNull();
    });

    it("shows unavailable for the license section when the status request fails, without hiding the version info", async () => {
      mockConfig("saas");
      mockServerInfoDefault();
      vi.mocked(licenseService.getStatus).mockRejectedValue(
        new Error("Network Error"),
      );

      renderAboutSection();

      await waitFor(() =>
        expect(
          screen.getByTestId("about-section-license-error"),
        ).toBeInTheDocument(),
      );
      expect(
        screen.getByTestId("about-section-server-version"),
      ).toHaveTextContent("1.92.0");
    });

    it("maps a missing license key to the existing 'missing_key' label", async () => {
      mockConfig("saas");
      mockServerInfoDefault();
      mockLicenseStatus({
        state: "missing_key",
        reason: null,
        offline: false,
        type: null,
        license_ends_at: null,
        expires_at: null,
        last_checked_at: null,
        customer_center_url: "https://customer.example.com",
        features: { sso: false },
      });

      renderAboutSection();

      await waitFor(() =>
        expect(
          screen.getByTestId("about-section-license-status"),
        ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_LICENSE_STATE_MISSING_KEY),
      );
      expect(
        screen.getByTestId("about-section-license-type"),
      ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_VALUE_UNAVAILABLE);
    });
  });
});
