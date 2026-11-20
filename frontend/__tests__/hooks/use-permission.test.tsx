import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { organizationService } from "#/api/organization-service/organization-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { OrganizationMember, OrganizationUserRole } from "#/types/org";

vi.mock("react-router", () => ({
  useRevalidator: () => ({ revalidate: vi.fn() }),
}));

const mockMe = (
  overrides: Partial<OrganizationMember> = {},
): OrganizationMember => ({
  org_id: "org-1",
  user_id: "user-1",
  email: "me@example.com",
  role: "member",
  llm_api_key: "",
  max_iterations: 100,
  llm_model: "gpt-4",
  llm_base_url: "",
  status: "active",
  ...overrides,
});

// Renders the hook once `/me` has loaded for the selected org.
const setup = async (me: OrganizationMember) => {
  const getMeSpy = vi.spyOn(organizationService, "getMe").mockResolvedValue(me);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const { result } = renderHook(() => usePermission(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => expect(getMeSpy).toHaveBeenCalled());
  await waitFor(() =>
    expect(queryClient.getQueryData(["organizations", "org-1", "me"])).toEqual(
      me,
    ),
  );
  return result;
};

describe("usePermission", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(OptionService, "getConfig").mockResolvedValue({
      app_mode: "saas",
    } as Awaited<ReturnType<typeof OptionService.getConfig>>);
    useSelectedOrganizationStore.setState({ organizationId: "org-1" });
  });

  it("returns true when /me grants the permission", async () => {
    const result = await setup(
      mockMe({ role: "admin", permissions: ["invite_user_to_organization"] }),
    );

    await waitFor(() =>
      expect(result.current.hasPermission("invite_user_to_organization")).toBe(
        true,
      ),
    );
  });

  it("returns false when /me does not grant the permission", async () => {
    const result = await setup(
      mockMe({ role: "admin", permissions: ["manage_secrets"] }),
    );

    await waitFor(() =>
      expect(result.current.hasPermission("manage_secrets")).toBe(true),
    );
    expect(result.current.hasPermission("invite_user_to_organization")).toBe(
      false,
    );
  });

  it("uses /me permissions, not the role", async () => {
    const result = await setup(
      mockMe({ role: "member", permissions: ["change_user_role:owner"] }),
    );

    await waitFor(() =>
      expect(result.current.hasPermission("change_user_role:owner")).toBe(true),
    );
  });

  it("grants nothing when /me has no permissions list (fail closed)", async () => {
    const result = await setup(mockMe({ role: "owner" }));

    expect(result.current.hasPermission("manage_secrets")).toBe(false);
    expect(result.current.hasPermission("delete_organization")).toBe(false);
  });

  it("does not throw for an unknown role", async () => {
    const result = await setup(
      mockMe({
        role: "service_account" as OrganizationUserRole,
        permissions: ["manage_secrets"],
      }),
    );

    await waitFor(() =>
      expect(result.current.hasPermission("manage_secrets")).toBe(true),
    );
    expect(result.current.hasPermission("view_billing")).toBe(false);
  });
});
