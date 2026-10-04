import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import SuperAdminInstallCompany from "#/routes/super-admin-install-company";
import { readImageFileAsDataUrl } from "#/utils/org/instance-logo";
import { resetSuperAdminNux } from "#/utils/org/super-admin-nux";

// jsdom cannot decode images, so resizing the picked file is stubbed.
vi.mock("#/utils/org/instance-logo", () => ({
  readImageFileAsDataUrl: vi.fn(),
}));

const SAVED_LOGO = "data:image/jpeg;base64,c2F2ZWQ=";
const UPLOADED_LOGO = "data:image/jpeg;base64,dXBsb2FkZWQ=";

function mockInstanceSettings(logo: string | null) {
  vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
    company_name: null,
    logo,
  });
}

async function renderCompanyStep() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The install layout's loader caches the config before this step renders.
  await queryClient.prefetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
  });
  const RouterStub = createRoutesStub([
    { path: "/install/company", Component: SuperAdminInstallCompany },
    {
      path: "/install/org",
      Component: () => <div data-testid="install-org-step" />,
    },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={["/install/company"]} />
    </QueryClientProvider>,
  );
  await screen.findByTestId("super-admin-install-company");
}

describe("super admin install company step", () => {
  beforeEach(() => {
    resetSuperAdminNux();
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        feature_flags: {
          ...createMockWebClientConfig().feature_flags,
          enable_super_admin: true,
        },
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("saves the company name and logo, then opens the organization step", async () => {
    // Arrange
    mockInstanceSettings(null);
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockResolvedValue({ company_name: "Acme", logo: UPLOADED_LOGO });
    vi.mocked(readImageFileAsDataUrl).mockResolvedValue(UPLOADED_LOGO);
    const user = userEvent.setup();
    await renderCompanyStep();
    await user.upload(
      screen.getByTestId("sa-nux-company-logo-input"),
      new File(["logo"], "logo.png", { type: "image/png" }),
    );
    await within(screen.getByTestId("sa-nux-company-logo")).findByRole(
      "presentation",
    );
    await user.type(screen.getByTestId("sa-nux-company-name"), "Acme");

    // Act
    await user.click(screen.getByTestId("sa-nux-company-continue"));

    // Assert
    expect(await screen.findByTestId("install-org-step")).toBeInTheDocument();
    expect(updateInstanceSettings).toHaveBeenCalledWith({
      company_name: "Acme",
      logo: UPLOADED_LOGO,
    });
  });

  it("shows the saved logo when the admin returns to the step", async () => {
    // Arrange
    mockInstanceSettings(SAVED_LOGO);

    // Act
    await renderCompanyStep();

    // Assert
    const preview = await within(
      screen.getByTestId("sa-nux-company-logo"),
    ).findByRole("presentation");
    expect(preview).toHaveAttribute("src", SAVED_LOGO);
  });

  it("keeps the saved logo when no new image is chosen", async () => {
    // Arrange
    mockInstanceSettings(SAVED_LOGO);
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockResolvedValue({ company_name: "Acme", logo: SAVED_LOGO });
    const user = userEvent.setup();
    await renderCompanyStep();
    await user.type(screen.getByTestId("sa-nux-company-name"), "Acme");

    // Act
    await user.click(screen.getByTestId("sa-nux-company-continue"));

    // Assert
    expect(await screen.findByTestId("install-org-step")).toBeInTheDocument();
    expect(updateInstanceSettings).toHaveBeenCalledWith({
      company_name: "Acme",
    });
  });

  it("stays on the company step when the settings cannot be saved", async () => {
    // Arrange
    mockInstanceSettings(null);
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockRejectedValue(new Error("Forbidden"));
    const user = userEvent.setup();
    await renderCompanyStep();
    await user.type(screen.getByTestId("sa-nux-company-name"), "Acme");
    const continueButton = screen.getByTestId("sa-nux-company-continue");

    // Act
    await user.click(continueButton);

    // Assert
    await waitFor(() => expect(continueButton).toBeEnabled());
    expect(updateInstanceSettings).toHaveBeenCalled();
    expect(screen.queryByTestId("install-org-step")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-install-company"),
    ).toBeInTheDocument();
  });
});
