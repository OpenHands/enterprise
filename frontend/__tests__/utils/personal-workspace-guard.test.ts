import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("react-router", () => ({
  replace: vi.fn((path: string) => ({ type: "replace", path })),
}));

const mockConfig = {
  app_mode: "saas" as "saas" | "oss",
  feature_flags: {} as { allow_user_llm_configuration?: boolean },
};

vi.mock("#/api/option-service/option-service.api", () => ({
  default: { getConfig: vi.fn(async () => mockConfig) },
}));

const mockOrganizations = {
  items: [
    { id: "personal-org", name: "Personal", is_personal: true },
    { id: "team-org", name: "Acme", is_personal: false },
  ],
  currentOrgId: "team-org" as string | null,
};

vi.mock("#/api/organization-service/organization-service.api", () => ({
  organizationService: {
    getOrganizations: vi.fn(async () => mockOrganizations),
  },
}));

let storeOrgId: string | null = null;
vi.mock("#/stores/selected-organization-store", () => ({
  getSelectedOrganizationIdFromStore: () => storeOrgId,
}));

vi.mock("#/query-client-config", () => ({
  queryClient: {
    fetchQuery: vi.fn(async (opts: { queryFn: () => Promise<unknown> }) =>
      opts.queryFn(),
    ),
  },
}));

import { replace } from "react-router";
import { requireOrgDefaultsRedirect as requirePersonalWorkspaceLoader } from "#/utils/org/saas-redirect-to-org-defaults-guard";

const createRequest = (pathname: string) => ({
  request: new Request(`http://localhost${pathname}`),
});

describe("requirePersonalWorkspaceLoader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConfig.app_mode = "saas";
    mockConfig.feature_flags = {};
    mockOrganizations.currentOrgId = "team-org";
    storeOrgId = null;
  });

  it("redirects to the org-defaults equivalent when the active org is a team workspace", async () => {
    storeOrgId = "team-org";
    const guard = requirePersonalWorkspaceLoader("/settings/org-defaults");

    const result = await guard(createRequest("/settings"));

    expect(replace).toHaveBeenCalledWith("/settings/org-defaults");
    expect(result).toEqual({ type: "replace", path: "/settings/org-defaults" });
  });

  it("redirects to the org-defaults equivalent even when the active org is the personal workspace", async () => {
    storeOrgId = "personal-org";
    const guard = requirePersonalWorkspaceLoader("/settings/org-defaults");

    const result = await guard(createRequest("/settings"));

    expect(replace).toHaveBeenCalledWith("/settings/org-defaults");
    expect(result).toEqual({ type: "replace", path: "/settings/org-defaults" });
  });

  it("keeps managed personal LLM selection reachable without changing other redirects", async () => {
    mockConfig.feature_flags.allow_user_llm_configuration = false;
    const guard = requirePersonalWorkspaceLoader("/settings/org-defaults", {
      allowManagedSelection: true,
    });
    expect(await guard(createRequest("/settings"))).toBeNull();
    const condenser = requirePersonalWorkspaceLoader(
      "/settings/org-defaults/condenser",
    );
    expect(await condenser(createRequest("/settings/condenser"))).toEqual({
      type: "replace",
      path: "/settings/org-defaults/condenser",
    });
  });

  it("skips the guard entirely in OSS mode", async () => {
    mockConfig.app_mode = "oss";
    storeOrgId = "team-org";
    const guard = requirePersonalWorkspaceLoader("/settings/org-defaults");

    const result = await guard(createRequest("/settings"));

    expect(result).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("does not redirect while the settings loader is consuming a pending ?org= switch", async () => {
    storeOrgId = "team-org";
    const guard = requirePersonalWorkspaceLoader("/settings/org-defaults");

    const result = await guard(createRequest("/settings?org=personal-org"));

    expect(result).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });
});
