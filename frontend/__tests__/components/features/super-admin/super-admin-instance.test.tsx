import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminInstance } from "#/components/features/super-admin/super-admin-pages";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { readImageFileAsDataUrl } from "#/utils/org/instance-logo";

// jsdom cannot decode images, so resizing the picked file is stubbed.
vi.mock("#/utils/org/instance-logo", () => ({
  readImageFileAsDataUrl: vi.fn(),
}));

const SAVED_LOGO = "data:image/jpeg;base64,c2F2ZWQ=";
const UPLOADED_LOGO = "data:image/jpeg;base64,dXBsb2FkZWQ=";

function mockSuperAdminConfig() {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      feature_flags: {
        ...createMockWebClientConfig().feature_flags,
        enable_super_admin: true,
      },
    }),
  );
}

async function renderInstancePage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The Super Admin layout renders its pages only once the config has loaded.
  await queryClient.prefetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
  });
  const RouterStub = createRoutesStub([
    { path: "/super-admin/instance", Component: SuperAdminInstance },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={["/super-admin/instance"]} />
    </QueryClientProvider>,
  );
  await screen.findByTestId("super-admin-instance");
}

describe("Super Admin Instance page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("links to the SAML SSO documentation", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );

    // Act
    await renderInstancePage();

    // Assert
    const link = screen.getByRole("link", {
      name: "SUPER_ADMIN$INSTANCE_SSO_DOCS",
    });
    expect(link).toHaveAttribute(
      "href",
      "https://docs.openhands.dev/enterprise/integrations/saml-sso",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("shows personal workspaces as allowed when the deployment does not hide them", async () => {
    // Arrange
    const config = createMockWebClientConfig();
    config.feature_flags.hide_personal_workspaces = false;
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(config);

    // Act
    await renderInstancePage();

    // Assert
    expect(
      screen.getByLabelText("SUPER_ADMIN$INSTANCE_AUTO_ORG"),
    ).toBeChecked();
  });

  it("shows personal workspaces as not allowed when the deployment hides them", async () => {
    // Arrange
    const config = createMockWebClientConfig();
    config.feature_flags.hide_personal_workspaces = true;
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(config);

    // Act
    await renderInstancePage();

    // Assert
    expect(
      screen.getByLabelText("SUPER_ADMIN$INSTANCE_AUTO_ORG"),
    ).not.toBeChecked();
  });

  it("saves an uploaded logo for the whole instance", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: null,
      logo: null,
    });
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockResolvedValue({ company_name: null, logo: UPLOADED_LOGO });
    vi.mocked(readImageFileAsDataUrl).mockResolvedValue(UPLOADED_LOGO);
    const user = userEvent.setup();
    await renderInstancePage();

    // Act
    await user.upload(
      screen.getByTestId("instance-logo-input"),
      new File(["logo"], "logo.png", { type: "image/png" }),
    );

    // Assert
    expect(
      await screen.findByTestId("instance-logo-remove"),
    ).toBeInTheDocument();
    expect(updateInstanceSettings).toHaveBeenCalledWith({
      logo: UPLOADED_LOGO,
    });
  });

  it("removes the saved logo from the instance", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: "Acme",
      logo: SAVED_LOGO,
    });
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockResolvedValue({ company_name: "Acme", logo: null });
    const user = userEvent.setup();
    await renderInstancePage();

    // Act
    await user.click(await screen.findByTestId("instance-logo-remove"));

    // Assert
    await waitFor(() =>
      expect(
        screen.queryByTestId("instance-logo-remove"),
      ).not.toBeInTheDocument(),
    );
    expect(updateInstanceSettings).toHaveBeenCalledWith({ logo: null });
  });
});
