import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSuperAdminOpenOrg } from "#/components/features/super-admin/use-super-admin-open-org";

const navigate = vi.fn();
const setOrganizationId = vi.fn();
const switchOrganization = vi.fn();
const setSelectedOrg = vi.fn();

let organizationId: string | null = "2";

vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>(
    "react-router",
  );
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock("#/context/use-selected-organization", () => ({
  useSelectedOrganizationId: () => ({
    organizationId,
    setOrganizationId,
  }),
}));

vi.mock("#/hooks/mutation/use-switch-organization", () => ({
  useSwitchOrganization: () => ({ mutate: switchOrganization }),
}));

vi.mock("#/utils/local-storage", () => ({
  setSelectedOrg: (orgId: string) => setSelectedOrg(orgId),
}));

describe("useSuperAdminOpenOrg", () => {
  beforeEach(() => {
    organizationId = "2";
    navigate.mockReset();
    setOrganizationId.mockReset();
    switchOrganization.mockReset();
    setSelectedOrg.mockReset();
  });

  it("switches into an org the super admin does not belong to and opens it", () => {
    const { result } = renderHook(() => useSuperAdminOpenOrg());

    result.current("5", "Northwind Labs");

    expect(setOrganizationId).toHaveBeenCalledWith("5", {
      skipRevalidation: true,
    });
    expect(setSelectedOrg).toHaveBeenCalledWith("5");
    expect(switchOrganization).toHaveBeenCalledWith({
      orgId: "5",
      orgName: "Northwind Labs",
      isPersonal: false,
    });
    expect(navigate).toHaveBeenCalledWith("/settings/usage-monitoring");
  });

  it("opens the already selected org without switching again", () => {
    organizationId = "5";
    const { result } = renderHook(() => useSuperAdminOpenOrg());

    result.current("5", "Northwind Labs");

    expect(setOrganizationId).not.toHaveBeenCalled();
    expect(switchOrganization).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("/settings/usage-monitoring");
  });
});
