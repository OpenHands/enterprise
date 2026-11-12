import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import OptionService from "#/api/option-service/option-service.api";
import type { WebClientConfig } from "#/api/option-service/option.types";
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

function mockSuperAdminConfig(overrides: { app_mode?: "saas" } = {}) {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      ...overrides,
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
  return queryClient;
}

describe("Super Admin Instance page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says when the install uses an offline license, and when it ends", async () => {
    // Arrange
    mockSuperAdminConfig({ app_mode: "saas" });
    server.use(
      http.get("*/api/license/status", () =>
        HttpResponse.json({
          state: "valid",
          reason: null,
          offline: true,
          type: "paid",
          license_ends_at: "2027-03-01T12:00:00Z",
          expires_at: "2027-03-15T12:00:00Z",
          last_checked_at: "2026-11-01T12:00:00Z",
          customer_center_url: "https://customercenter.openhands.dev",
        }),
      ),
    );

    // Act
    await renderInstancePage();

    // Assert
    const license = await screen.findByTestId("instance-offline-license");
    expect(license).toHaveTextContent("SUPER_ADMIN$INSTANCE_OFFLINE_LICENSE");
    expect(license).toHaveTextContent("SUPER_ADMIN$INSTANCE_LICENSE_ENDS_AT");
  });

  it("says nothing about the license with an online license", async () => {
    // Arrange
    mockSuperAdminConfig({ app_mode: "saas" });
    server.use(
      http.get("*/api/license/status", () =>
        HttpResponse.json({ state: "valid", offline: false }),
      ),
    );

    // Act
    await renderInstancePage();

    // Assert
    expect(
      screen.queryByTestId("instance-offline-license"),
    ).not.toBeInTheDocument();
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

    it("sends PATCH for the flag and shows the saved value", async () => {
      // Arrange
      mockServiceAccountsConfig(true);
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
      await waitFor(() => expect(toggle).not.toBeChecked());
    });

    it("keeps the saved value when a later config read is stale", async () => {
      // Arrange — another server process still caches the old flag value, so
      // a config read after the save would return it.
      const getConfig = mockServiceAccountsConfig(true, true);
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

      // Assert — the PATCH response is the saved value; no config read
      // overwrites it.
      await waitFor(() => expect(toggle).toBeEnabled());
      expect(toggle).not.toBeChecked();
      expect(getConfig).toHaveBeenCalledTimes(1);
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

  describe.each([
    {
      label: "Enable Agent Profiles",
      testId: "super-admin-instance-enable-agent-profiles",
      labelKey: "SUPER_ADMIN$INSTANCE_ENABLE_AGENT_PROFILES",
      flagKey: "ENABLE_AGENT_PROFILES",
      configFlag: "enable_agent_profiles_page",
    },
    {
      label: "Enable Organization MCP Servers",
      testId: "super-admin-instance-enable-org-mcp-servers",
      labelKey: "SUPER_ADMIN$INSTANCE_ENABLE_ORG_MCP_SERVERS",
      flagKey: "ENABLE_ORG_MCP_SERVERS",
      configFlag: "enable_org_mcp_servers",
    },
  ] as const)(
    "$label switch",
    ({ label, testId, labelKey, flagKey, configFlag }) => {
      const FLAG_URL = "/api/admin/feature-flags";

      function mockSwitchConfig(value: boolean) {
        const config = createMockWebClientConfig();
        config.feature_flags[configFlag] = value;
        vi.spyOn(OptionService, "getConfig").mockResolvedValue(config);
      }

      it("renders with its label and reflects the resolved config value", async () => {
        // Arrange
        mockSwitchConfig(true);

        // Act
        await renderInstancePage();

        // Assert
        const toggle = screen.getByLabelText(labelKey);
        expect(toggle).toHaveAttribute("data-testid", testId);
        expect(toggle).toBeChecked();
        expect(translations[labelKey].en).toBe(label);
      });

      it("sends PATCH for its flag and shows the saved value", async () => {
        // Arrange
        mockSwitchConfig(true);
        const patchBodies: unknown[] = [];
        server.use(
          http.patch(`${FLAG_URL}/:key`, async ({ params, request }) => {
            patchBodies.push({ key: params.key, body: await request.json() });
            return HttpResponse.json({
              key: params.key,
              description: null,
              enabled: false,
            });
          }),
        );
        const user = userEvent.setup();
        await renderInstancePage();
        const toggle = screen.getByTestId(testId);

        // Act
        await user.click(toggle);

        // Assert
        await waitFor(() =>
          expect(patchBodies).toEqual([
            { key: flagKey, body: { enabled: false } },
          ]),
        );
        await waitFor(() => expect(toggle).not.toBeChecked());
      });

      it("creates the flag with POST when PATCH answers 404", async () => {
        // Arrange
        mockSwitchConfig(false);
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
        const toggle = screen.getByTestId(testId);

        // Act
        await user.click(toggle);

        // Assert
        await waitFor(() =>
          expect(postBodies).toEqual([
            { key: flagKey, description: label, enabled: true },
          ]),
        );
        await waitFor(() => expect(toggle).toBeChecked());
      });

      it("is disabled while the change is saving", async () => {
        // Arrange
        mockSwitchConfig(false);
        let release: () => void = () => {};
        server.use(
          http.patch(
            `${FLAG_URL}/:key`,
            () =>
              new Promise<Response>((resolve) => {
                release = () =>
                  resolve(
                    HttpResponse.json({
                      key: flagKey,
                      description: null,
                      enabled: true,
                    }),
                  );
              }),
          ),
        );
        const user = userEvent.setup();
        await renderInstancePage();
        const toggle = screen.getByTestId(testId);

        // Act
        await user.click(toggle);

        // Assert
        await waitFor(() => expect(toggle).toBeDisabled());
        release();
        await waitFor(() => expect(toggle).not.toBeDisabled());
      });
    },
  );

  it("turns on every Agent Profiles config flag when its switch is saved", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );
    server.use(
      http.patch("/api/admin/feature-flags/:key", ({ params }) =>
        HttpResponse.json({
          key: params.key,
          description: null,
          enabled: true,
        }),
      ),
    );
    const user = userEvent.setup();
    const queryClient = await renderInstancePage();

    // Act
    await user.click(
      screen.getByTestId("super-admin-instance-enable-agent-profiles"),
    );

    // Assert
    await waitFor(() =>
      expect(
        queryClient.getQueryData<WebClientConfig>(QUERY_KEYS.WEB_CLIENT_CONFIG)
          ?.feature_flags,
      ).toMatchObject({
        enable_agent_profiles_page: true,
        enable_agent_context_page: true,
        enable_org_agent_profiles: true,
      }),
    );
  });
});
