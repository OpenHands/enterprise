import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, afterEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { AboutSection } from "#/components/features/settings/app-settings/about-section";
import OptionService from "#/api/option-service/option-service.api";
import { I18nKey } from "#/i18n/declaration";
import {
  licenseService,
  LicenseStatus,
} from "#/api/license-service/license-service.api";

vi.mock("#/api/option-service/option-service.api", () => ({
  default: {
    getReleaseInfo: vi.fn(),
  },
}));

vi.mock("#/api/license-service/license-service.api", () => ({
  licenseService: {
    getStatus: vi.fn(),
  },
}));

// Pure OSS/cloud-SaaS by default: the license section and enterprise-release
// row must not render (or query /api/license/status), matching the existing
// LicenseBanner gating. Pass true for self-hosted-enterprise scenarios.
const renderAboutSection = (isEnterpriseSelfHosted = false) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AboutSection isEnterpriseSelfHosted={isEnterpriseSelfHosted} />
    </QueryClientProvider>,
  );
};

const mockClipboard = (writeText: ReturnType<typeof vi.fn>) => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
};

const mockReleaseInfoDefault = () =>
  vi.mocked(OptionService.getReleaseInfo).mockResolvedValue({
    app_version: "1.92.0",
    sdk_version: "1.62.0",
    enterprise_release: null,
  });

const mockLicenseStatus = (status: LicenseStatus) =>
  vi.mocked(licenseService.getStatus).mockResolvedValue(status);

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AboutSection", () => {
  it("renders the server and SDK component versions from /api/v1/release", async () => {
    mockReleaseInfoDefault();

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

  it("shows the customer-facing enterprise release as primary when present (self-hosted enterprise)", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockResolvedValue({
      app_version: "1.94.1",
      sdk_version: "1.62.0",
      enterprise_release: "0.110.0",
    });

    renderAboutSection(true);

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-enterprise-release"),
      ).toHaveTextContent("0.110.0"),
    );
  });

  it("hides the enterprise-release row entirely on cloud SaaS / pure OSS (not self-hosted enterprise)", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockResolvedValue({
      app_version: "1.94.1",
      sdk_version: "1.62.0",
      enterprise_release: null,
    });

    renderAboutSection(false);

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-server-version"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByTestId("about-section-enterprise-release"),
    ).not.toBeInTheDocument();
  });

  it("honestly shows the unavailable label for the enterprise release when packaging hasn't set it, without inferring it from app_version", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockResolvedValue({
      app_version: "1.94.1",
      sdk_version: "1.62.0",
      enterprise_release: null,
    });

    renderAboutSection(true);

    await waitFor(() =>
      expect(
        screen.getByTestId("about-section-enterprise-release"),
      ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_VALUE_UNAVAILABLE),
    );
    expect(
      screen.getByTestId("about-section-enterprise-release"),
    ).not.toHaveTextContent("1.94.1");
  });

  it("shows an unavailable message when the request fails (retried once, per useReleaseInfo)", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockRejectedValue(
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
    mockReleaseInfoDefault();

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

  it("restarts the copied confirmation after another successful copy", async () => {
    mockReleaseInfoDefault();
    mockClipboard(vi.fn().mockResolvedValue(undefined));
    const { unmount } = renderAboutSection();
    const button = await screen.findByTestId("about-section-copy-button");

    vi.useFakeTimers();
    try {
      await act(async () => {
        fireEvent.click(button);
      });
      act(() => vi.advanceTimersByTime(1500));
      await act(async () => {
        fireEvent.click(button);
      });
      act(() => vi.advanceTimersByTime(600));
      expect(button).toHaveTextContent(I18nKey.SETTINGS$ABOUT_COPIED);
      act(() => vi.advanceTimersByTime(1400));
      expect(button).toHaveTextContent(I18nKey.SETTINGS$ABOUT_COPY_BUTTON);
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not mark itself 'copied' when the clipboard write is rejected", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    mockClipboard(writeText);
    mockReleaseInfoDefault();

    renderAboutSection();

    const copyButton = await screen.findByTestId("about-section-copy-button");
    await user.click(copyButton);

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    // A rejected clipboard write must not crash the component or flip it
    // into the "copied" state; it stays on the default copy label.
    expect(copyButton).toHaveTextContent(I18nKey.SETTINGS$ABOUT_COPY_BUTTON);
  });

  it("falls back to the unavailable label per-field when a value is missing (older server)", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockResolvedValue({
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

  describe("license section (self-hosted enterprise only)", () => {
    it("does not render or query license status on cloud SaaS / pure OSS", async () => {
      mockReleaseInfoDefault();

      renderAboutSection(false);

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

    it("localizes the displayed license type but copies stable support values", async () => {
      const user = userEvent.setup();
      const writeText = vi.fn().mockResolvedValue(undefined);
      mockClipboard(writeText);
      mockReleaseInfoDefault();
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

      renderAboutSection(true);

      await waitFor(() =>
        expect(
          screen.getByTestId("about-section-license-status"),
        ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_LICENSE_STATE_VALID),
      );
      expect(
        screen.getByTestId("about-section-license-type"),
      ).toHaveTextContent(I18nKey.SETTINGS$ABOUT_LICENSE_TYPE_TRIAL);
      await user.click(screen.getByTestId("about-section-copy-button"));
      expect(writeText).toHaveBeenCalledWith(
        [
          "OpenHands Enterprise release: unavailable",
          "App server component version: 1.92.0",
          "Agent SDK component version: 1.62.0",
          "License status: valid",
          "License type: trial",
        ].join("\n"),
      );
      // The customer_center_url and any key hint are never rendered here.
      expect(screen.queryByText(/customer\.example\.com/)).toBeNull();
    });

    it("falls back to the raw string for an undocumented license type", async () => {
      mockReleaseInfoDefault();
      mockLicenseStatus({
        state: "valid",
        reason: null,
        offline: false,
        type: "enterprise-plus",
        license_ends_at: null,
        expires_at: null,
        last_checked_at: null,
        customer_center_url: "https://customer.example.com",
        features: { sso: true },
      });

      renderAboutSection(true);

      await waitFor(() =>
        expect(
          screen.getByTestId("about-section-license-type"),
        ).toHaveTextContent("enterprise-plus"),
      );
    });

    it("shows unavailable for the license section when the status request fails, without hiding the version info", async () => {
      mockReleaseInfoDefault();
      vi.mocked(licenseService.getStatus).mockRejectedValue(
        new Error("Network Error"),
      );

      renderAboutSection(true);

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
      mockReleaseInfoDefault();
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

      renderAboutSection(true);

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
