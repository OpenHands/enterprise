import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCanManageOrgAgentProfiles } from "#/hooks/organizations/use-can-manage-org-agent-profiles";
import { organizationService } from "#/api/organization-service/organization-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { OrganizationMember } from "#/types/org";

vi.mock("react-router", () => ({
  useRevalidator: () => ({ revalidate: vi.fn() }),
}));

const mockMe = (permissions: string[]): OrganizationMember =>
  ({
    org_id: "org-1",
    user_id: "user-1",
    email: "me@example.com",
    role: "admin",
    llm_api_key: "",
    max_iterations: 100,
    llm_model: "gpt-4",
    llm_base_url: "",
    status: "active",
    permissions,
  }) as OrganizationMember;

// Renders the hook once both `/me` and the config have loaded.
const setup = async (permissions: string[], orgProfilesFlag: boolean) => {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue({
    app_mode: "saas",
    feature_flags: { enable_org_agent_profiles: orgProfilesFlag },
  } as Awaited<ReturnType<typeof OptionService.getConfig>>);
  const getMeSpy = vi
    .spyOn(organizationService, "getMe")
    .mockResolvedValue(mockMe(permissions));
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const { result } = renderHook(() => useCanManageOrgAgentProfiles(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => expect(getMeSpy).toHaveBeenCalled());
  return { result, queryClient };
};

describe("useCanManageOrgAgentProfiles", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useSelectedOrganizationStore.setState({ organizationId: "org-1" });
  });

  // The backend guards org-scope agent profile writes with edit_org_settings,
  // which /me returns; there is no separate agent-profile permission.
  it("is true for edit_org_settings when org profiles are on", async () => {
    const { result } = await setup(["edit_org_settings"], true);

    await waitFor(() => expect(result.current).toBe(true));
  });

  it("is false without edit_org_settings", async () => {
    const { result, queryClient } = await setup(["view_org_settings"], true);

    await waitFor(() =>
      expect(queryClient.getQueryData(["organizations", "org-1", "me"])).toBeTruthy(),
    );
    expect(result.current).toBe(false);
  });

  it("is false when org profiles are off", async () => {
    const { result, queryClient } = await setup(["edit_org_settings"], false);

    await waitFor(() =>
      expect(queryClient.getQueryData(["organizations", "org-1", "me"])).toBeTruthy(),
    );
    expect(result.current).toBe(false);
  });
});
