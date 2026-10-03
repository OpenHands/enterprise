import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { SuperAdminInstance } from "#/components/features/super-admin/super-admin-pages";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

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
});
