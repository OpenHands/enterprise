import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import OptionService from "#/api/option-service/option-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminInstance } from "#/components/features/super-admin/super-admin-pages";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import translations from "#/i18n/translation.json";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { readImageFileAsDataUrl } from "#/utils/org/instance-logo";
import { server } from "#/mocks/node";

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

  it("does not show a SAML callout -- SAML is set up on the Auth Providers page", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );

    // Act
    await renderInstancePage();

    // Assert
    expect(
      screen.queryByTestId("super-admin-instance-sso"),
    ).not.toBeInTheDocument();
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

  it("does not show an email row -- email delivery is configured on the server, not here", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ email_enabled: true }),
    );

    // Act
    await renderInstancePage();

    // Assert
    expect(
      screen.queryByLabelText("SUPER_ADMIN$INSTANCE_EMAIL"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("SUPER_ADMIN$INSTANCE_AUTO_ORG"),
    ).toBeInTheDocument();
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

  it("tells the admin the company name shows at the top of the Settings left navigation", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: null,
      logo: null,
    });

    // Act
    await renderInstancePage();

    // Assert
    expect(screen.getByTestId("instance-company-name-hint")).toHaveTextContent(
      "SUPER_ADMIN$INSTANCE_COMPANY_NAME_HINT",
    );
    expect(translations.SUPER_ADMIN$INSTANCE_COMPANY_NAME_HINT.en).toContain(
      "at the top of the Settings left navigation",
    );
  });

  it("saves the trimmed company name when the field loses focus", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: "Acme",
      logo: null,
    });
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockResolvedValue({ company_name: "Acme Corp", logo: null });
    const user = userEvent.setup();
    await renderInstancePage();
    const input = await screen.findByTestId("instance-company-name");
    await waitFor(() => expect(input).toHaveValue("Acme"));

    // Act
    await user.clear(input);
    await user.type(input, "  Acme Corp ");
    await user.tab();

    // Assert
    expect(updateInstanceSettings).toHaveBeenCalledWith({
      company_name: "Acme Corp",
    });
    await waitFor(() => expect(input).toHaveValue("Acme Corp"));
  });

  it("clears the company name on Enter when the field is emptied", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: "Acme",
      logo: null,
    });
    const updateInstanceSettings = vi
      .spyOn(superAdminService, "updateInstanceSettings")
      .mockResolvedValue({ company_name: null, logo: null });
    const user = userEvent.setup();
    await renderInstancePage();
    const input = await screen.findByTestId("instance-company-name");
    await waitFor(() => expect(input).toHaveValue("Acme"));

    // Act
    await user.clear(input);
    await user.type(input, "{Enter}");

    // Assert
    expect(updateInstanceSettings).toHaveBeenCalledWith({
      company_name: null,
    });
  });

  it("does not save a company name that did not change", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getInstanceSettings").mockResolvedValue({
      company_name: "Acme",
      logo: null,
    });
    const updateInstanceSettings = vi.spyOn(
      superAdminService,
      "updateInstanceSettings",
    );
    const user = userEvent.setup();
    await renderInstancePage();
    const input = await screen.findByTestId("instance-company-name");
    await waitFor(() => expect(input).toHaveValue("Acme"));

    // Act
    await user.type(input, " ");
    await user.tab();

    // Assert
    expect(updateInstanceSettings).not.toHaveBeenCalled();
  });

  it("saves hiding the setup guide on the server", async () => {
    // Arrange
    mockSuperAdminConfig();
    vi.spyOn(superAdminService, "getSetupState").mockResolvedValue({
      wizard_pending: false,
      guide_org_id: "guide-org",
      guide_dismissed: false,
      guide_steps: null,
    });
    const updateSetupState = vi
      .spyOn(superAdminService, "updateSetupState")
      .mockResolvedValue({
        wizard_pending: false,
        guide_org_id: "guide-org",
        guide_dismissed: true,
        guide_steps: null,
      });
    const user = userEvent.setup();
    await renderInstancePage();
    const setupGuideSwitch = await screen.findByTestId(
      "super-admin-instance-setup-guide",
    );
    await waitFor(() => expect(setupGuideSwitch).toBeChecked());

    // Act
    await user.click(setupGuideSwitch);

    // Assert
    expect(updateSetupState).toHaveBeenCalledWith({ guide_dismissed: true });
    await waitFor(() => expect(setupGuideSwitch).not.toBeChecked());
  });

  describe("Enable Service Accounts switch", () => {
    const FLAG_URL = "/api/admin/feature-flags";

    function mockServiceAccountsConfig(...values: boolean[]) {
      const spy = vi.spyOn(OptionService, "getConfig");
      values.forEach((value) => {
        const config = createMockWebClientConfig();
        config.feature_flags.enable_service_accounts = value;
        spy.mockResolvedValueOnce(config);
      });
      return spy;
    }

    it("renders with its label and reflects the resolved config value", async () => {
      // Arrange
      mockServiceAccountsConfig(true);

      // Act
      await renderInstancePage();

      // Assert
      const toggle = screen.getByLabelText(
        "SUPER_ADMIN$INSTANCE_ENABLE_SERVICE_ACCOUNTS",
      );
      expect(toggle).toHaveAttribute(
        "data-testid",
        "super-admin-instance-enable-service-accounts",
      );
      expect(toggle).toBeChecked();
      expect(translations.SUPER_ADMIN$INSTANCE_ENABLE_SERVICE_ACCOUNTS.en).toBe(
        "Enable Service Accounts (beta)",
      );
    });

    it("is off when the config has no enable_service_accounts", async () => {
      // Arrange
      const config = createMockWebClientConfig();
      delete config.feature_flags.enable_service_accounts;
      vi.spyOn(OptionService, "getConfig").mockResolvedValue(config);

      // Act
      await renderInstancePage();

      // Assert
      expect(
        screen.getByTestId("super-admin-instance-enable-service-accounts"),
      ).not.toBeChecked();
    });

    it("sends PATCH for the flag and refetches the config", async () => {
      // Arrange
      const getConfig = mockServiceAccountsConfig(true, false);
      const patchBodies: unknown[] = [];
      const postSpy = vi.fn();
      server.use(
        http.patch(`${FLAG_URL}/:key`, async ({ params, request }) => {
          const body = await request.json();
          patchBodies.push({ key: params.key, body });
          return HttpResponse.json({
            key: params.key,
            description: null,
            enabled: false,
          });
        }),
        http.post(FLAG_URL, () => {
          postSpy();
          return HttpResponse.json({}, { status: 201 });
        }),
      );
      const user = userEvent.setup();
      await renderInstancePage();
      const toggle = screen.getByTestId(
        "super-admin-instance-enable-service-accounts",
      );
      expect(toggle).toBeChecked();

      // Act
      await user.click(toggle);

      // Assert
      await waitFor(() =>
        expect(patchBodies).toEqual([
          { key: "ENABLE_SERVICE_ACCOUNTS", body: { enabled: false } },
        ]),
      );
      expect(postSpy).not.toHaveBeenCalled();
      await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(toggle).not.toBeChecked());
    });

    it("shows the saved value and stays disabled until the config refetch returns", async () => {
      // Arrange — the refetch after the save is held until we release it.
      const first = createMockWebClientConfig();
      first.feature_flags.enable_service_accounts = true;
      let releaseRefetch: (value: typeof first) => void = () => {};
      vi.spyOn(OptionService, "getConfig")
        .mockResolvedValueOnce(first)
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              releaseRefetch = resolve;
            }),
        );
      server.use(
        http.patch(`${FLAG_URL}/:key`, ({ params }) =>
          HttpResponse.json({
            key: params.key,
            description: null,
            enabled: false,
          }),
        ),
      );
      const user = userEvent.setup();
      await renderInstancePage();
      const toggle = screen.getByTestId(
        "super-admin-instance-enable-service-accounts",
      );

      // Act
      await user.click(toggle);

      // Assert — saved value shown, no second click while the refetch runs.
      await waitFor(() => expect(toggle).not.toBeChecked());
      expect(toggle).toBeDisabled();
      const refetched = createMockWebClientConfig();
      refetched.feature_flags.enable_service_accounts = false;
      releaseRefetch(refetched);
      await waitFor(() => expect(toggle).toBeEnabled());
      expect(toggle).not.toBeChecked();
    });

    it("creates the flag with POST when PATCH answers 404", async () => {
      // Arrange
      mockServiceAccountsConfig(false, true);
      const postBodies: unknown[] = [];
      server.use(
        http.patch(`${FLAG_URL}/:key`, () =>
          HttpResponse.json({ detail: "Flag not found" }, { status: 404 }),
        ),
        http.post(FLAG_URL, async ({ request }) => {
          const body = await request.json();
          postBodies.push(body);
          return HttpResponse.json(
            { ...(body as object), rules: [] },
            { status: 201 },
          );
        }),
      );
      const user = userEvent.setup();
      await renderInstancePage();
      const toggle = screen.getByTestId(
        "super-admin-instance-enable-service-accounts",
      );
      expect(toggle).not.toBeChecked();

      // Act
      await user.click(toggle);

      // Assert
      await waitFor(() =>
        expect(postBodies).toEqual([
          {
            key: "ENABLE_SERVICE_ACCOUNTS",
            description: "Enable Service Accounts (beta)",
            enabled: true,
          },
        ]),
      );
      await waitFor(() => expect(toggle).toBeChecked());
    });

    it("is disabled while the change is saving", async () => {
      // Arrange
      mockServiceAccountsConfig(false, true);
      let release: () => void = () => {};
      server.use(
        http.patch(
          `${FLAG_URL}/:key`,
          () =>
            new Promise<Response>((resolve) => {
              release = () =>
                resolve(
                  HttpResponse.json({
                    key: "ENABLE_SERVICE_ACCOUNTS",
                    description: null,
                    enabled: true,
                  }),
                );
            }),
        ),
      );
      const user = userEvent.setup();
      await renderInstancePage();
      const toggle = screen.getByTestId(
        "super-admin-instance-enable-service-accounts",
      );

      // Act
      await user.click(toggle);

      // Assert
      await waitFor(() => expect(toggle).toBeDisabled());
      release();
      await waitFor(() => expect(toggle).not.toBeDisabled());
    });
  });
});
