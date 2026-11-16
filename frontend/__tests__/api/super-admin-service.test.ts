import { beforeEach, describe, expect, it, vi } from "vitest";
import { openHands } from "#/api/open-hands-axios";
import {
  superAdminService,
  type SuperAdminApiAdmin,
  type SuperAdminApiOrg,
  type SuperAdminApiUser,
} from "#/api/super-admin-service/super-admin-service.api";

vi.mock("#/api/open-hands-axios", () => ({
  openHands: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

type Verb = "get" | "post" | "patch" | "delete";

const ADMIN: SuperAdminApiAdmin = { user_id: "7", email: "grace@acme.org" };

const ORG: SuperAdminApiOrg = {
  id: "org-1",
  name: "Acme Corp",
  contact_email: "ops@acme.org",
  contact_name: null,
  member_count: 3,
  is_personal: false,
  status: "active",
};

const USER: SuperAdminApiUser = {
  user_id: "7",
  email: "grace@acme.org",
  name: "grace",
  memberships: [],
  status: "active",
  is_super_admin: false,
};

// Each request must match the backend route in server/routes/instance_admin.py,
// server/routes/super_admins.py or server/routes/user_provisioning.py.
const REQUESTS: {
  method: string;
  call: () => Promise<unknown>;
  verb: Verb;
  args: unknown[];
}[] = [
  {
    method: "listSuperAdmins",
    call: () => superAdminService.listSuperAdmins(),
    verb: "get",
    args: ["/api/admin/super-admins?page_id=0&limit=20"],
  },
  {
    method: "listSuperAdmins (page 3)",
    call: () => superAdminService.listSuperAdmins({ page: 3, limit: 10 }),
    verb: "get",
    args: ["/api/admin/super-admins?page_id=20&limit=10"],
  },
  {
    method: "grantSuperAdmin (by email)",
    call: () => superAdminService.grantSuperAdmin({ email: "ada@acme.org" }),
    verb: "post",
    args: ["/api/admin/super-admins", { email: "ada@acme.org" }],
  },
  {
    // The Users page already has the target's id, so it grants by id
    // instead of looking the account up by email again.
    method: "grantSuperAdmin (by userId)",
    call: () => superAdminService.grantSuperAdmin({ userId: "7" }),
    verb: "post",
    args: ["/api/admin/super-admins", { user_id: "7" }],
  },
  {
    method: "revokeSuperAdmin",
    call: () => superAdminService.revokeSuperAdmin({ userId: "7" }),
    verb: "delete",
    args: ["/api/admin/super-admins/7"],
  },
  {
    method: "listOrganizations",
    call: () => superAdminService.listOrganizations(),
    verb: "get",
    args: ["/api/admin/organizations?page_id=0&limit=20"],
  },
  {
    method: "listOrganizations (search, no personal workspaces)",
    call: () =>
      superAdminService.listOrganizations({
        page: 2,
        limit: 100,
        q: "ac me",
        includePersonal: false,
      }),
    verb: "get",
    args: [
      "/api/admin/organizations?page_id=100&limit=100&q=ac+me&include_personal=false",
    ],
  },
  {
    method: "deleteOrganization",
    call: () => superAdminService.deleteOrganization({ orgId: "org-1" }),
    verb: "delete",
    args: ["/api/admin/organizations/org-1"],
  },
  {
    method: "listUsers",
    call: () => superAdminService.listUsers(),
    verb: "get",
    args: ["/api/admin/directory/users?page_id=0&limit=20"],
  },
  {
    method: "listUsers (page 2, search)",
    call: () => superAdminService.listUsers({ page: 2, limit: 5, q: "ada" }),
    verb: "get",
    args: ["/api/admin/directory/users?page_id=5&limit=5&q=ada"],
  },
  {
    method: "getUser",
    call: () => superAdminService.getUser({ userId: "7" }),
    verb: "get",
    args: ["/api/admin/directory/users/7"],
  },
  {
    method: "updateUserStatus",
    call: () =>
      superAdminService.updateUserStatus({ userId: "7", status: "inactive" }),
    verb: "patch",
    args: ["/api/admin/directory/users/7", { status: "inactive" }],
  },
  {
    method: "removeUser",
    call: () => superAdminService.removeUser({ userId: "7" }),
    verb: "delete",
    args: ["/api/admin/directory/users/7"],
  },
  {
    method: "updateUserGroups",
    call: () =>
      superAdminService.updateUserGroups({
        userId: "7",
        action: "set_role",
        orgIds: ["org-1"],
        role: "admin",
      }),
    verb: "post",
    args: [
      "/api/admin/directory/users/7/groups",
      { action: "set_role", org_ids: ["org-1"], role: "admin" },
    ],
  },
  {
    method: "getInstanceSettings",
    call: () => superAdminService.getInstanceSettings(),
    verb: "get",
    args: ["/api/admin/instance-settings"],
  },
  {
    method: "updateInstanceSettings",
    call: () =>
      superAdminService.updateInstanceSettings({ company_name: "Acme" }),
    verb: "patch",
    args: ["/api/admin/instance-settings", { company_name: "Acme" }],
  },
  {
    method: "getSetupState",
    call: () => superAdminService.getSetupState(),
    verb: "get",
    args: ["/api/admin/setup-state"],
  },
  {
    method: "updateSetupState",
    call: () => superAdminService.updateSetupState({ guide_dismissed: true }),
    verb: "patch",
    args: ["/api/admin/setup-state", { guide_dismissed: true }],
  },
];

describe("superAdminService", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each(REQUESTS)(
    "$method sends the request the backend route expects",
    async ({ call, verb, args }) => {
      // Arrange
      vi.mocked(openHands[verb]).mockResolvedValue({
        data: { super_admins: [], organizations: [], users: [] },
      });

      // Act
      await call();

      // Assert
      expect(openHands[verb]).toHaveBeenCalledWith(...args);
    },
  );

  it.each([
    {
      method: "listSuperAdmins",
      call: () => superAdminService.listSuperAdmins(),
      items: [ADMIN],
    },
    {
      method: "listOrganizations",
      call: () => superAdminService.listOrganizations(),
      items: [ORG],
    },
    {
      method: "listUsers",
      call: () => superAdminService.listUsers(),
      items: [USER],
    },
  ])(
    "$method returns the whole page, with its position",
    async ({ call, items }) => {
      // Arrange
      const page = { items, total: 41, current_page: 3, per_page: 20 };
      vi.mocked(openHands.get).mockResolvedValue({ data: page });

      // Act
      const result = await call();

      // Assert
      expect(result).toEqual(page);
    },
  );
});
