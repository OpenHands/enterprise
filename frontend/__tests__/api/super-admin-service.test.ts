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
    args: ["/api/admin/super-admins"],
  },
  {
    method: "grantSuperAdmin",
    call: () => superAdminService.grantSuperAdmin({ email: "ada@acme.org" }),
    verb: "post",
    args: ["/api/admin/super-admins", { email: "ada@acme.org" }],
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
    args: ["/api/admin/organizations"],
  },
  {
    method: "deleteOrganization",
    call: () => superAdminService.deleteOrganization({ orgId: "org-1" }),
    verb: "delete",
    args: ["/api/admin/organizations/org-1"],
  },
  {
    method: "updateOrganizationStatus",
    call: () =>
      superAdminService.updateOrganizationStatus({
        orgId: "org-1",
        status: "suspended",
      }),
    verb: "patch",
    args: ["/api/admin/organizations/org-1", { status: "suspended" }],
  },
  {
    method: "listUsers",
    call: () => superAdminService.listUsers(),
    verb: "get",
    args: ["/api/admin/users"],
  },
  {
    method: "updateUserStatus",
    call: () =>
      superAdminService.updateUserStatus({ userId: "7", status: "inactive" }),
    verb: "patch",
    args: ["/api/admin/users/7", { status: "inactive" }],
  },
  {
    method: "removeUser",
    call: () => superAdminService.removeUser({ userId: "7" }),
    verb: "delete",
    args: ["/api/admin/users/7"],
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
      "/api/admin/users/7/groups",
      { action: "set_role", org_ids: ["org-1"], role: "admin" },
    ],
  },
  {
    method: "provisionUser",
    call: () =>
      superAdminService.provisionUser({
        orgId: "org-1",
        payload: { email: "ada@acme.org", role: "member" },
      }),
    verb: "post",
    args: [
      "/api/organizations/provision-user",
      { email: "ada@acme.org", role: "member" },
      { headers: { "X-Org-Id": "org-1" } },
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
      data: { super_admins: [ADMIN] },
      expected: [ADMIN],
    },
    {
      method: "listOrganizations",
      call: () => superAdminService.listOrganizations(),
      data: { organizations: [ORG] },
      expected: [ORG],
    },
    {
      method: "listUsers",
      call: () => superAdminService.listUsers(),
      data: { users: [USER] },
      expected: [USER],
    },
  ])(
    "$method returns the rows inside the response envelope",
    async ({ call, data, expected }) => {
      // Arrange
      vi.mocked(openHands.get).mockResolvedValue({ data });

      // Act
      const rows = await call();

      // Assert
      expect(rows).toEqual(expected);
    },
  );
});
