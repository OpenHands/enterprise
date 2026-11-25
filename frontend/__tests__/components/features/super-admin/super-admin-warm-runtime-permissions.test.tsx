import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import type { SuperAdminApiOrg } from "#/api/super-admin-service/super-admin-service.api";
import { warmRuntimesService } from "#/api/warm-runtimes-service/warm-runtimes-service.api";
import { SuperAdminWarmRuntimePermissions } from "#/components/features/super-admin/super-admin-warm-runtime-permissions";

const org = (id: string, name: string): SuperAdminApiOrg => ({
  id,
  name,
  contact_email: null,
  contact_name: null,
  member_count: 1,
  is_personal: false,
  status: "active",
});

const ACME = org("acme-id", "Acme");
const GLOBEX = org("globex-id", "Globex");

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/warm-runtimes/permissions/:name",
      Component: SuperAdminWarmRuntimePermissions,
    },
    {
      path: "/super-admin/warm-runtimes",
      Component: () => <p data-testid="list-page" />,
    },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub
        initialEntries={["/super-admin/warm-runtimes/permissions/large"]}
      />
    </QueryClientProvider>,
  );
}

const saveButton = () =>
  screen.getByTestId("super-admin-warm-runtime-permissions-submit");

describe("Super Admin warm runtime permissions", () => {
  beforeEach(() => {
    vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue({
      items: [ACME, GLOBEX],
      total: 2,
      current_page: 1,
      per_page: 100,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a public runtime without an organization list", async () => {
    vi.spyOn(warmRuntimesService, "getWarmRuntimeAccess").mockResolvedValue({
      is_private: false,
      orgs: [],
    });
    renderPage();

    const toggle = await screen.findByTestId(
      "super-admin-warm-runtime-private",
    );

    expect(toggle).not.toBeChecked();
    expect(
      screen.queryByTestId("super-admin-warm-runtime-add-org"),
    ).not.toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("makes a runtime private and grants it to an organization", async () => {
    const user = userEvent.setup();
    vi.spyOn(warmRuntimesService, "getWarmRuntimeAccess").mockResolvedValue({
      is_private: false,
      orgs: [],
    });
    const update = vi
      .spyOn(warmRuntimesService, "updateWarmRuntimeAccess")
      .mockResolvedValue({
        is_private: true,
        orgs: [{ id: ACME.id, name: ACME.name }],
      });
    renderPage();

    await user.click(
      await screen.findByTestId("super-admin-warm-runtime-private"),
    );
    expect(
      screen.getByTestId("super-admin-warm-runtime-no-allowed-orgs"),
    ).toBeInTheDocument();
    await user.click(screen.getByTestId("super-admin-warm-runtime-add-org"));
    await user.click(await screen.findByText("Acme"));
    await user.click(saveButton());

    await waitFor(() =>
      expect(update).toHaveBeenCalledExactlyOnceWith({
        name: "large",
        is_private: true,
        org_ids: [ACME.id],
      }),
    );
  });

  it("lists the granted organizations and revokes one", async () => {
    const user = userEvent.setup();
    vi.spyOn(warmRuntimesService, "getWarmRuntimeAccess").mockResolvedValue({
      is_private: true,
      orgs: [
        { id: ACME.id, name: ACME.name },
        { id: GLOBEX.id, name: GLOBEX.name },
      ],
    });
    const update = vi
      .spyOn(warmRuntimesService, "updateWarmRuntimeAccess")
      .mockResolvedValue({ is_private: true, orgs: [] });
    renderPage();

    expect(
      await screen.findByTestId(
        `super-admin-warm-runtime-allowed-org-${ACME.id}`,
      ),
    ).toHaveTextContent("Acme");
    await user.click(
      screen.getByTestId(`super-admin-warm-runtime-remove-org-${ACME.id}`),
    );
    await user.click(saveButton());

    await waitFor(() =>
      expect(update).toHaveBeenCalledExactlyOnceWith({
        name: "large",
        is_private: true,
        org_ids: [GLOBEX.id],
      }),
    );
  });

  it("drops the grants when the runtime is made public again", async () => {
    const user = userEvent.setup();
    vi.spyOn(warmRuntimesService, "getWarmRuntimeAccess").mockResolvedValue({
      is_private: true,
      orgs: [{ id: ACME.id, name: ACME.name }],
    });
    const update = vi
      .spyOn(warmRuntimesService, "updateWarmRuntimeAccess")
      .mockResolvedValue({ is_private: false, orgs: [] });
    renderPage();

    await user.click(
      await screen.findByTestId("super-admin-warm-runtime-private"),
    );
    await user.click(saveButton());

    await waitFor(() =>
      expect(update).toHaveBeenCalledExactlyOnceWith({
        name: "large",
        is_private: false,
        org_ids: [],
      }),
    );
  });

  it("explains when the runtime cannot be loaded", async () => {
    vi.spyOn(warmRuntimesService, "getWarmRuntimeAccess").mockRejectedValue(
      new Error("not found"),
    );
    renderPage();

    expect(
      await screen.findByTestId("super-admin-warm-runtime-permissions-error"),
    ).toBeInTheDocument();
  });
});
