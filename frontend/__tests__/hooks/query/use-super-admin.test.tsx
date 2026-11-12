import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import {
  superAdminService,
  type SuperAdminPage,
  type SetupState,
  type SuperAdminApiAdmin,
  type SuperAdminApiOrg,
  type SuperAdminApiUser,
} from "#/api/super-admin-service/super-admin-service.api";
import {
  useRemoveSuperAdminUser,
  useUpdateSetupState,
  useUpdateSuperAdminUserGroups,
  useUpdateSuperAdminUserStatus,
} from "#/hooks/mutation/use-super-admin-mutations";
import { useConfig } from "#/hooks/query/use-config";
import {
  useSetupState,
  useSuperAdminTeamOrganizations,
  useSuperAdmins,
  useSuperAdminUsers,
} from "#/hooks/query/use-super-admin";
import { useSuperAdminUsage } from "#/hooks/query/use-super-admin-usage";

function pageOf<T>(items: T[], total = items.length): SuperAdminPage<T> {
  return { items, total, current_page: 1, per_page: 20 };
}

const PENDING_SETUP_STATE: SetupState = {
  wizard_pending: true,
  guide_org_id: null,
  guide_dismissed: false,
  guide_steps: null,
};

function mockSuperAdminFlag(enabled: boolean) {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue({
    app_mode: "saas",
    feature_flags: { enable_super_admin: enabled },
  } as Awaited<ReturnType<typeof OptionService.getConfig>>);
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return Wrapper;
}

describe("useSetupState", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the signed-in user's setup state when the Super Admin flag is on", async () => {
    // Arrange
    mockSuperAdminFlag(true);
    vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(
      PENDING_SETUP_STATE,
    );

    // Act
    const { result } = renderHook(() => useSetupState(), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() =>
      expect(result.current.data).toEqual(PENDING_SETUP_STATE),
    );
  });

  it("does not request the setup state when the Super Admin flag is off", async () => {
    // Arrange
    mockSuperAdminFlag(false);
    const getSetupState = vi.spyOn(superAdminService, "getSetupState");

    // Act
    const { result } = renderHook(
      () => ({ config: useConfig(), setupState: useSetupState() }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(result.current.config.isSuccess).toBe(true));

    // Assert
    expect(getSetupState).not.toHaveBeenCalled();
    expect(result.current.setupState.data).toBeUndefined();
  });

  it("returns the saved state after an update", async () => {
    // Arrange
    mockSuperAdminFlag(true);
    vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(
      PENDING_SETUP_STATE,
    );
    const savedState = { ...PENDING_SETUP_STATE, wizard_pending: false };
    const updateSetupState = vi
      .spyOn(superAdminService, "updateSetupState")
      .mockResolvedValue(savedState);
    const { result } = renderHook(
      () => ({ setupState: useSetupState(), update: useUpdateSetupState() }),
      { wrapper: createWrapper() },
    );
    await waitFor(() =>
      expect(result.current.setupState.data).toEqual(PENDING_SETUP_STATE),
    );

    // Act
    await act(async () => {
      await result.current.update.mutateAsync({ wizard_completed: true });
    });

    // Assert
    expect(updateSetupState).toHaveBeenCalledWith({ wizard_completed: true });
    await waitFor(() =>
      expect(result.current.setupState.data).toEqual(savedState),
    );
  });
});

describe("useRemoveSuperAdminUser", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("refreshes the Super Admin list after deleting a Super Admin", async () => {
    // Arrange
    const admin: SuperAdminApiAdmin = {
      user_id: "admin-2",
      email: "admin2@acme.dev",
    };
    vi.spyOn(superAdminService, "listSuperAdmins")
      .mockResolvedValueOnce(pageOf([admin]))
      .mockResolvedValueOnce(pageOf([]));
    vi.spyOn(superAdminService, "removeUser").mockResolvedValue({
      message: "User deleted",
      user_id: admin.user_id,
    });
    const { result } = renderHook(
      () => ({
        admins: useSuperAdmins(),
        removeUser: useRemoveSuperAdminUser(),
      }),
      { wrapper: createWrapper() },
    );
    await waitFor(() =>
      expect(result.current.admins.data?.items).toEqual([admin]),
    );

    // Act
    await act(async () => {
      await result.current.removeUser.mutateAsync({ userId: admin.user_id });
    });

    // Assert
    await waitFor(() => expect(result.current.admins.data?.items).toEqual([]));
  });
});

const GRACE: SuperAdminApiUser = {
  user_id: "7",
  email: "grace@acme.org",
  name: "grace",
  memberships: [
    { org_id: "org-1", org_name: "Acme", role: "member", status: "active" },
  ],
  status: "active",
  is_super_admin: false,
};

describe("useUpdateSuperAdminUserGroups", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("changes the user's groups and refreshes the user directory", async () => {
    // Arrange
    const promoted: SuperAdminApiUser = {
      ...GRACE,
      memberships: [{ ...GRACE.memberships[0], role: "admin" }],
    };
    vi.spyOn(superAdminService, "listUsers")
      .mockResolvedValueOnce(pageOf([GRACE]))
      .mockResolvedValue(pageOf([promoted]));
    const updateUserGroups = vi
      .spyOn(superAdminService, "updateUserGroups")
      .mockResolvedValue(promoted);
    const { result } = renderHook(
      () => ({
        users: useSuperAdminUsers(),
        updateGroups: useUpdateSuperAdminUserGroups(),
      }),
      { wrapper: createWrapper() },
    );
    await waitFor(() =>
      expect(result.current.users.data?.items).toEqual([GRACE]),
    );

    // Act
    await act(async () => {
      await result.current.updateGroups.mutateAsync({
        userId: "7",
        action: "set_role",
        orgIds: ["org-1"],
        role: "admin",
      });
    });

    // Assert
    expect(updateUserGroups).toHaveBeenCalledWith({
      userId: "7",
      action: "set_role",
      orgIds: ["org-1"],
      role: "admin",
    });
    await waitFor(() =>
      expect(result.current.users.data?.items).toEqual([promoted]),
    );
  });
});

describe("useUpdateSuperAdminUserStatus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("suspends the account and refreshes the user directory", async () => {
    // Arrange
    const suspended: SuperAdminApiUser = { ...GRACE, status: "inactive" };
    vi.spyOn(superAdminService, "listUsers")
      .mockResolvedValueOnce(pageOf([GRACE]))
      .mockResolvedValue(pageOf([suspended]));
    const updateUserStatus = vi
      .spyOn(superAdminService, "updateUserStatus")
      .mockResolvedValue(suspended);
    const { result } = renderHook(
      () => ({
        users: useSuperAdminUsers(),
        updateStatus: useUpdateSuperAdminUserStatus(),
      }),
      { wrapper: createWrapper() },
    );
    await waitFor(() =>
      expect(result.current.users.data?.items).toEqual([GRACE]),
    );

    // Act
    await act(async () => {
      await result.current.updateStatus.mutateAsync({
        userId: "7",
        status: "inactive",
      });
    });

    // Assert
    expect(updateUserStatus).toHaveBeenCalledWith({
      userId: "7",
      status: "inactive",
    });
    await waitFor(() =>
      expect(result.current.users.data?.items).toEqual([suspended]),
    );
  });
});

describe("useSuperAdminUsage", () => {
  type UsageStats = Awaited<
    ReturnType<typeof organizationService.getUsageStats>
  >;
  type ConversationPage = Awaited<
    ReturnType<typeof organizationService.getConversations>
  >;

  const team = (id: string, name: string): SuperAdminApiOrg => ({
    id,
    name,
    contact_email: null,
    contact_name: null,
    member_count: 2,
    is_personal: false,
    status: "active",
  });
  const ACME = team("acme", "Acme");
  const GLOBEX = team("globex", "Globex");
  const PERSONAL_WORKSPACE: SuperAdminApiOrg = {
    ...team("user-7", "user_7_org"),
    is_personal: true,
  };

  const usageStats = (conversations: number, spend: number): UsageStats => ({
    active_users: 1,
    agent_runs: 0,
    usage_conversation_count: conversations,
    total_tokens: 0,
    estimated_spend: spend,
    daily_usage: [],
    team_usage: [],
    model_usage: [],
    agent_usage: [],
  });

  const ACME_CONVERSATION: ConversationPage["items"][number] = {
    id: "conv-1",
    title: "Fix the build",
    llm_model: null,
    agent_kind: "openhands",
    user_id: "7",
    user_email: "grace@acme.org",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    sandbox_id: null,
    sandbox_status: null,
    runtime_url: null,
    execution_status: "finished",
    selected_repository: null,
    selected_branch: null,
    git_provider: null,
    trigger: null,
    pr_number: [],
    pr_merged: null,
    tags: {},
    accumulated_cost: 1.5,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  };

  function mockOrgUsage() {
    const stats: Record<string, UsageStats> = {
      acme: usageStats(3, 1.5),
      globex: usageStats(2, 2.25),
    };
    vi.spyOn(superAdminService, "listOrganizations").mockImplementation(
      async ({ includePersonal = true } = {}) =>
        pageOf(
          [ACME, GLOBEX, PERSONAL_WORKSPACE].filter(
            (org) => includePersonal || !org.is_personal,
          ),
        ),
    );
    const getUsageStats = vi
      .spyOn(organizationService, "getUsageStats")
      .mockImplementation(async ({ orgId }) => stats[orgId]);
    vi.spyOn(organizationService, "getUserUsageStats").mockResolvedValue({
      items: [],
      has_more: false,
    });
    const getConversations = vi
      .spyOn(organizationService, "getConversations")
      .mockImplementation(async ({ orgId }) => ({
        items: orgId === "acme" ? [ACME_CONVERSATION] : [],
        total_items: orgId === "acme" ? 1 : 0,
        page: 1,
        per_page: 100,
        total_pages: 1,
      }));
    return { getUsageStats, getConversations };
  }

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("adds up usage across team organizations and skips personal workspaces", async () => {
    // Arrange
    const { getUsageStats } = mockOrgUsage();

    // Act
    const { result } = renderHook(
      () => useSuperAdminUsage({ selectedOrgIds: [], timeWindow: "30d" }),
      { wrapper: createWrapper() },
    );

    // Assert
    await waitFor(() => expect(result.current.usage.conversations).toBe(5));
    expect(result.current.usage.spend).toBe(3.75);
    expect(result.current.usage.conversationRows).toEqual([
      expect.objectContaining({
        id: "conv-1",
        org_id: "acme",
        org_name: "Acme",
      }),
    ]);
    expect(result.current.orgs).toEqual([
      { id: "acme", name: "Acme" },
      { id: "globex", name: "Globex" },
    ]);
    expect(getUsageStats).not.toHaveBeenCalledWith(
      expect.objectContaining({ orgId: PERSONAL_WORKSPACE.id }),
    );
  });

  it("requests usage only for the selected organizations", async () => {
    // Arrange
    const { getUsageStats } = mockOrgUsage();

    // Act
    const { result } = renderHook(
      () =>
        useSuperAdminUsage({ selectedOrgIds: ["globex"], timeWindow: "30d" }),
      { wrapper: createWrapper() },
    );

    // Assert
    await waitFor(() => expect(result.current.usage.conversations).toBe(2));
    expect(getUsageStats).toHaveBeenCalledTimes(1);
    expect(getUsageStats).toHaveBeenCalledWith({
      orgId: "globex",
      timeWindow: "30d",
    });
  });

  it("requests year-to-date stats and every recent conversation for the ytd window", async () => {
    // Arrange
    const { getUsageStats, getConversations } = mockOrgUsage();

    // Act
    const { result } = renderHook(
      () => useSuperAdminUsage({ selectedOrgIds: ["acme"], timeWindow: "ytd" }),
      { wrapper: createWrapper() },
    );

    // Assert
    await waitFor(() => expect(result.current.usage.conversations).toBe(3));
    expect(getUsageStats).toHaveBeenCalledWith({
      orgId: "acme",
      timeWindow: "ytd",
    });
    expect(getConversations).toHaveBeenCalledWith({
      orgId: "acme",
      page: 1,
      perPage: 100,
      sortBy: "updated_at",
      sortOrder: "desc",
      timeWindow: undefined,
    });
  });
});

describe("useSuperAdminUsers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests the page and search it is given, keeping each page apart", async () => {
    // Arrange
    const listUsers = vi
      .spyOn(superAdminService, "listUsers")
      .mockImplementation(async ({ page } = {}) =>
        pageOf([{ ...GRACE, user_id: `user-${page}` }], 41),
      );

    // Act
    const { result, rerender } = renderHook(
      ({ page }) => useSuperAdminUsers({ page, limit: 20, q: "gra" }),
      { wrapper: createWrapper(), initialProps: { page: 1 } },
    );
    await waitFor(() =>
      expect(result.current.data?.items[0].user_id).toBe("user-1"),
    );
    rerender({ page: 2 });

    // Assert
    await waitFor(() =>
      expect(result.current.data?.items[0].user_id).toBe("user-2"),
    );
    expect(listUsers).toHaveBeenCalledWith({ page: 1, limit: 20, q: "gra" });
    expect(listUsers).toHaveBeenCalledWith({ page: 2, limit: 20, q: "gra" });
    expect(result.current.data?.total).toBe(41);
  });

  it("stays idle while disabled", async () => {
    // Arrange
    const listUsers = vi.spyOn(superAdminService, "listUsers");

    // Act
    renderHook(() => useSuperAdminUsers({}, false), {
      wrapper: createWrapper(),
    });

    // Assert
    expect(listUsers).not.toHaveBeenCalled();
  });
});

describe("useSuperAdminTeamOrganizations", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("gathers every team organization one full page at a time", async () => {
    // Arrange
    const orgs = Array.from({ length: 230 }, (_, index) => ({
      id: `org-${index}`,
      name: `Org ${index}`,
      contact_email: null,
      contact_name: null,
      member_count: 1,
      is_personal: false,
      status: "active" as const,
    }));
    const listOrganizations = vi
      .spyOn(superAdminService, "listOrganizations")
      .mockImplementation(async ({ page = 1, limit = 20 } = {}) =>
        pageOf(orgs.slice((page - 1) * limit, page * limit), orgs.length),
      );

    // Act
    const { result } = renderHook(() => useSuperAdminTeamOrganizations(), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() => expect(result.current.data).toHaveLength(230));
    expect(listOrganizations.mock.calls.map(([params]) => params)).toEqual([
      { page: 1, limit: 100, includePersonal: false },
      { page: 2, limit: 100, includePersonal: false },
      { page: 3, limit: 100, includePersonal: false },
    ]);
  });
});
