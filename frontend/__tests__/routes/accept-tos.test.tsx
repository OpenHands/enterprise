import { render, screen } from "@testing-library/react";
import { it, describe, expect, vi, beforeEach, afterEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AcceptTOS from "#/routes/accept-tos";
import * as CaptureConsent from "#/utils/handle-capture-consent";
import { openHands } from "#/api/open-hands-axios";
import OptionService from "#/api/option-service/option-service.api";
import type { DeploymentMode } from "#/api/option-service/option.types";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { getSignupEntry, rememberCloudEntry } from "#/utils/signup-attribution";

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router")>()),
  useNavigate: () => vi.fn(),
  useSearchParams: () => [
    {
      get: (param: string) => {
        if (param === "redirect_url") {
          return "/dashboard";
        }
        return null;
      },
    },
  ],
  useRevalidator: () => ({ revalidate: vi.fn() }),
}));

vi.mock("#/api/open-hands-axios", () => ({
  openHands: {
    post: vi.fn(),
  },
}));

// The global mock of `t` drops interpolation values. Keep them, so a test can
// check which product the heading names.
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-i18next")>()),
  useTranslation: () => ({
    t: (key: string, options?: Record<string, string>) =>
      options ? `${key} ${JSON.stringify(options)}` : key,
    i18n: { language: "en", exists: () => false },
  }),
}));

vi.mock("#/utils/custom-toast-handlers", () => ({
  displayErrorToast: vi.fn(),
}));

// Web-client config of an enterprise install with the given deployment mode
const createSaasConfig = (deploymentMode?: DeploymentMode) => {
  const config = createMockWebClientConfig({ app_mode: "saas" });
  return {
    ...config,
    feature_flags: { ...config.feature_flags, deployment_mode: deploymentMode },
  };
};

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }

  return Wrapper;
};

describe("AcceptTOS", () => {
  beforeEach(() => {
    sessionStorage.clear();
    useSelectedOrganizationStore.setState({ organizationId: "test-org-id" });
    vi.stubGlobal("location", {
      href: "",
      origin: "http://localhost",
      pathname: "/accept-tos",
      search: "",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetAllMocks();
  });

  it("should render a TOS checkbox that is unchecked by default", () => {
    render(<AcceptTOS />, { wrapper: createWrapper() });

    const checkbox = screen.getByRole("checkbox");
    const continueButton = screen.getByRole("button", { name: "TOS$CONTINUE" });

    expect(checkbox).not.toBeChecked();
    expect(continueButton).toBeDisabled();
  });

  it("should enable the continue button when the TOS checkbox is checked", async () => {
    const user = userEvent.setup();
    render(<AcceptTOS />, { wrapper: createWrapper() });

    const checkbox = screen.getByRole("checkbox");
    const continueButton = screen.getByRole("button", { name: "TOS$CONTINUE" });

    expect(continueButton).toBeDisabled();

    await user.click(checkbox);

    expect(continueButton).not.toBeDisabled();
  });

  it("should set user analytics consent to true when the user accepts TOS", async () => {
    const handleCaptureConsentSpy = vi.spyOn(
      CaptureConsent,
      "handleCaptureConsent",
    );

    vi.mocked(openHands.post).mockResolvedValue({
      data: { redirect_url: "/dashboard" },
    });

    const user = userEvent.setup();
    render(<AcceptTOS />, { wrapper: createWrapper() });

    const checkbox = screen.getByRole("checkbox");
    await user.click(checkbox);

    const continueButton = screen.getByRole("button", { name: "TOS$CONTINUE" });
    await user.click(continueButton);

    await new Promise(process.nextTick);

    expect(handleCaptureConsentSpy).toHaveBeenCalledWith(
      expect.anything(),
      true,
    );
    expect(openHands.post).toHaveBeenCalledWith("/api/accept_tos", {
      redirect_url: "/dashboard",
    });
  });

  it("sends a validated website source and campaign with the signup request", async () => {
    rememberCloudEntry("marketing_website", {
      utm_source: "newsletter",
      utm_campaign: "launch",
    });
    vi.mocked(openHands.post).mockResolvedValue({
      data: { redirect_url: "/dashboard" },
    });
    const user = userEvent.setup();
    render(<AcceptTOS />, { wrapper: createWrapper() });
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "TOS$CONTINUE" }));

    await new Promise(process.nextTick);
    expect(openHands.post).toHaveBeenCalledWith("/api/accept_tos", {
      redirect_url: "/dashboard",
      signup_entry_source: "marketing_website",
      utm_source: "newsletter",
      utm_campaign: "launch",
    });
    expect(getSignupEntry()).toBeNull();
  });

  it("should handle external redirect URLs", async () => {
    // Mock the API response with an external URL
    const externalUrl = "https://example.com/callback";
    vi.mocked(openHands.post).mockResolvedValue({
      data: { redirect_url: externalUrl },
    });

    const user = userEvent.setup();
    render(<AcceptTOS />, { wrapper: createWrapper() });

    const checkbox = screen.getByRole("checkbox");
    await user.click(checkbox);

    const continueButton = screen.getByRole("button", { name: "TOS$CONTINUE" });
    await user.click(continueButton);

    await new Promise(process.nextTick);

    expect(window.location.href).toBe(externalUrl);
  });

  it("should name OpenHands Enterprise and link its terms on a self-hosted install", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createSaasConfig("self_hosted"),
    );

    render(<AcceptTOS />, { wrapper: createWrapper() });

    expect(
      await screen.findByRole("heading", {
        name: 'TOS$ACCEPT_PRODUCT_TERMS_OF_SERVICE {"product":"OpenHands Enterprise"}',
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "TOS$TERMS" })).toHaveAttribute(
      "href",
      "https://www.openhands.dev/enterprise-tos",
    );
  });

  it("should name OpenHands Cloud and link its terms on the managed cloud", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createSaasConfig("cloud"),
    );

    render(<AcceptTOS />, { wrapper: createWrapper() });

    expect(
      await screen.findByRole("heading", {
        name: 'TOS$ACCEPT_PRODUCT_TERMS_OF_SERVICE {"product":"OpenHands Cloud"}',
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "TOS$TERMS" })).toHaveAttribute(
      "href",
      "https://www.openhands.dev/tos",
    );
  });

  it("should keep the generic terms wording when the deployment mode is unknown", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(createSaasConfig());

    render(<AcceptTOS />, { wrapper: createWrapper() });

    expect(
      await screen.findByRole("heading", {
        name: "TOS$ACCEPT_TERMS_OF_SERVICE",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "TOS$TERMS" })).toHaveAttribute(
      "href",
      "https://www.openhands.dev/tos",
    );
  });
});
