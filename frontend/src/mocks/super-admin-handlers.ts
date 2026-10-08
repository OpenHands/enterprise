import { http, HttpResponse } from "msw";
import {
  InstanceSettings,
  SetupGuideSteps,
  SetupState,
  SetupStateUpdate,
} from "#/api/super-admin-service/super-admin-service.api";
import { requestWantsFreshSa } from "./mock-fresh-sa";

const MOCK_SUPER_ADMINS = [
  { user_id: "99", email: "me@acme.org" },
  { user_id: "u-5", email: "riley@all-hands.dev" },
];

let superAdmins = [...MOCK_SUPER_ADMINS];

// The mock user's personal workspace (MOCK_PERSONAL_ORG in org-handlers).
const MOCK_PERSONAL_ORG_ID = "1";

const MOCK_ADMIN_ORGS = [
  {
    id: "2",
    name: "Acme Corp",
    contact_email: "me@acme.org",
    contact_name: "openhands",
    member_count: 24,
    is_personal: false,
    status: "active" as const,
  },
  {
    id: "4",
    name: "All Hands AI",
    contact_email: "ops@all-hands.dev",
    contact_name: "Ops",
    member_count: 18,
    is_personal: false,
    status: "active" as const,
  },
  {
    id: "3",
    name: "Beta LLC",
    contact_email: "admin@beta.llc",
    contact_name: "Admin",
    member_count: 7,
    is_personal: false,
    status: "suspended" as const,
  },
];

let adminOrgs = [...MOCK_ADMIN_ORGS];

type MockAdminUser = {
  user_id: string;
  email: string;
  name: string;
  status: "active" | "inactive";
  memberships: {
    org_id: string;
    org_name: string;
    role: string;
    status: string;
  }[];
};

const MOCK_ADMIN_USERS: MockAdminUser[] = [
  {
    user_id: "99",
    email: "me@acme.org",
    name: "openhands",
    status: "active" as const,
    memberships: [
      {
        org_id: "2",
        org_name: "Acme Corp",
        role: "owner",
        status: "active",
      },
      {
        org_id: "4",
        org_name: "All Hands AI",
        role: "admin",
        status: "active",
      },
    ],
  },
  {
    user_id: "u-2",
    email: "alex@acme.org",
    name: "Alex Rivera",
    status: "active" as const,
    memberships: [
      {
        org_id: "2",
        org_name: "Acme Corp",
        role: "admin",
        status: "active",
      },
    ],
  },
];

let adminUsers = [...MOCK_ADMIN_USERS];
let freshSaAdminApplied = false;

const MOCK_INSTANCE_SETTINGS: InstanceSettings = {
  company_name: null,
  logo: null,
};

let instanceSettings = { ...MOCK_INSTANCE_SETTINGS };

// The mock user is the first Super Admin of a fresh install until the wizard ends.
const MOCK_SETUP_STATE: SetupState = {
  wizard_pending: true,
  guide_org_id: null,
  guide_dismissed: false,
  guide_steps: null,
};

const MOCK_GUIDE_STEPS: SetupGuideSteps = {
  org_llm: false,
  mcp_server: false,
  automation: false,
  invite: false,
};

let setupState = { ...MOCK_SETUP_STATE };

// Like the server, steps are reported only while the guide has an org and is not dismissed.
const setupStateResponse = (): SetupState => ({
  ...setupState,
  guide_steps:
    setupState.guide_org_id && !setupState.guide_dismissed
      ? MOCK_GUIDE_STEPS
      : null,
});

const FRESH_SA_ADMIN_ORG = {
  id: "2",
  name: "Acme Corp",
  contact_email: "me@acme.org",
  contact_name: "openhands",
  member_count: 1,
  is_personal: false,
  status: "active" as const,
};

const FRESH_SA_SECOND_ORG = {
  id: "5",
  name: "Northwind Labs",
  contact_email: "ops@northwind.example",
  contact_name: "Ops",
  member_count: 0,
  is_personal: false,
  status: "active" as const,
};

const FRESH_SA_ADMIN_USER: MockAdminUser = {
  user_id: "99",
  email: "me@acme.org",
  name: "openhands",
  status: "active",
  memberships: [
    {
      org_id: "2",
      org_name: "Acme Corp",
      role: "owner",
      status: "active",
    },
  ],
};

function applyFreshSaAdminSeed() {
  adminOrgs = [{ ...FRESH_SA_ADMIN_ORG }, { ...FRESH_SA_SECOND_ORG }];
  adminUsers = [
    {
      ...FRESH_SA_ADMIN_USER,
      memberships: FRESH_SA_ADMIN_USER.memberships.map((membership) => ({
        ...membership,
      })),
    },
  ];
  superAdmins = [{ user_id: "99", email: "me@acme.org" }];
  freshSaAdminApplied = true;
}

function ensureFreshSaAdminState(request: Request) {
  if (
    requestWantsFreshSa(request) ||
    import.meta.env.VITE_MOCK_FRESH_SA === "true"
  ) {
    if (!freshSaAdminApplied) {
      applyFreshSaAdminSeed();
    }
  } else if (freshSaAdminApplied) {
    superAdmins = [...MOCK_SUPER_ADMINS];
    adminOrgs = [...MOCK_ADMIN_ORGS];
    adminUsers = [...MOCK_ADMIN_USERS];
    freshSaAdminApplied = false;
  }
}

if (import.meta.env.VITE_MOCK_FRESH_SA === "true") {
  applyFreshSaAdminSeed();
}

type MockMembershipAdded = {
  userId: string;
  orgId: string;
  orgName: string;
  role: string;
};

const membershipAddedListeners = new Set<
  (event: MockMembershipAdded) => void
>();

/** Lets the org mock record a membership created from the admin groups API. */
export function onMockAdminMembershipAdded(
  listener: (event: MockMembershipAdded) => void,
) {
  membershipAddedListeners.add(listener);
}

/** Lets the org mock resolve `owner_user_id` like the server's user lookup. */
export function findMockAdminUser(userId: string) {
  return adminUsers.find((user) => user.user_id === userId);
}

export function registerMockAdminOrg(org: {
  id: string;
  name: string;
  contact_email?: string | null;
  contact_name?: string | null;
  owner_user_id?: string;
}) {
  if (adminOrgs.some((row) => row.id === org.id)) {
    return;
  }
  adminOrgs = [
    ...adminOrgs,
    {
      id: org.id,
      name: org.name,
      contact_email: org.contact_email ?? "",
      contact_name: org.contact_name ?? "",
      member_count: org.owner_user_id ? 1 : 0,
      is_personal: false,
      status: "active" as const,
    },
  ];
  // Like the server, the owner is the new organization's only member.
  adminUsers = adminUsers.map((user) =>
    user.user_id === org.owner_user_id
      ? {
          ...user,
          memberships: [
            ...user.memberships,
            {
              org_id: org.id,
              org_name: org.name,
              role: "owner",
              status: "active",
            },
          ],
        }
      : user,
  );
}

export const resetSuperAdminMockState = () => {
  superAdmins = [...MOCK_SUPER_ADMINS];
  adminOrgs = [...MOCK_ADMIN_ORGS];
  adminUsers = [...MOCK_ADMIN_USERS];
  freshSaAdminApplied = false;
  instanceSettings = { ...MOCK_INSTANCE_SETTINGS };
  setupState = { ...MOCK_SETUP_STATE };
};

export const SUPER_ADMIN_HANDLERS = [
  http.get("/api/admin/super-admins", () =>
    HttpResponse.json({ super_admins: superAdmins }),
  ),

  http.post("/api/admin/super-admins", async ({ request }) => {
    const body = (await request.json()) as {
      email?: string;
      user_id?: string;
    };
    const email = body.email?.trim().toLowerCase();
    if (!email && !body.user_id) {
      return HttpResponse.json(
        { detail: 'Provide exactly one of "user_id" or "email".' },
        { status: 400 },
      );
    }
    const existing = superAdmins.find(
      (admin) =>
        (email && admin.email?.toLowerCase() === email) ||
        (body.user_id && admin.user_id === body.user_id),
    );
    if (existing) {
      return HttpResponse.json(existing, { status: 201 });
    }
    const created = {
      user_id: body.user_id ?? `mock-${superAdmins.length + 1}`,
      email: email ?? "unknown@example.com",
    };
    superAdmins = [created, ...superAdmins];
    return HttpResponse.json(created, { status: 201 });
  }),

  http.delete("/api/admin/super-admins/:userId", ({ params }) => {
    const { userId } = params;
    if (superAdmins.length <= 1) {
      return HttpResponse.json(
        { detail: "Cannot remove the last remaining super admin" },
        { status: 409 },
      );
    }
    const target = superAdmins.find((admin) => admin.user_id === userId);
    if (!target) {
      return HttpResponse.json(
        { detail: "User is not a super admin" },
        { status: 404 },
      );
    }
    superAdmins = superAdmins.filter((admin) => admin.user_id !== userId);
    return HttpResponse.json(target);
  }),

  http.get("/api/admin/organizations", ({ request }) => {
    ensureFreshSaAdminState(request);
    return HttpResponse.json({ organizations: adminOrgs });
  }),

  http.patch("/api/admin/organizations/:orgId", async ({ params, request }) => {
    const { orgId } = params;
    const body = (await request.json()) as { status?: "active" | "suspended" };
    const target = adminOrgs.find((org) => org.id === orgId);
    if (!target) {
      return HttpResponse.json(
        { detail: "Organization not found" },
        { status: 404 },
      );
    }
    if (body.status !== "active" && body.status !== "suspended") {
      return HttpResponse.json(
        { detail: "Invalid organization status" },
        { status: 400 },
      );
    }
    target.status = body.status;
    return HttpResponse.json(target);
  }),

  http.delete("/api/admin/organizations/:orgId", ({ params }) => {
    const { orgId } = params;
    const target = adminOrgs.find((org) => org.id === orgId);
    if (!target) {
      return HttpResponse.json(
        { detail: "Organization not found" },
        { status: 404 },
      );
    }
    adminOrgs = adminOrgs.filter((org) => org.id !== orgId);
    adminUsers = adminUsers.map((user) => ({
      ...user,
      memberships: user.memberships.filter(
        (membership) => membership.org_id !== orgId,
      ),
    }));
    return HttpResponse.json({
      message: "Organization deleted successfully",
      organization: {
        id: target.id,
        name: target.name,
        contact_name: target.contact_name,
        contact_email: target.contact_email,
      },
    });
  }),

  http.get("/api/admin/directory/users", ({ request }) => {
    ensureFreshSaAdminState(request);
    return HttpResponse.json({ users: adminUsers });
  }),

  http.patch(
    "/api/admin/directory/users/:userId",
    async ({ params, request }) => {
      const { userId } = params;
      const body = (await request.json()) as { status?: "active" | "inactive" };
      const target = adminUsers.find((user) => user.user_id === userId);
      if (!target) {
        return HttpResponse.json({ detail: "User not found" }, { status: 404 });
      }
      if (body.status !== "active" && body.status !== "inactive") {
        return HttpResponse.json(
          { detail: "Invalid user status" },
          { status: 400 },
        );
      }
      target.status = body.status;
      return HttpResponse.json(target);
    },
  ),

  http.delete("/api/admin/directory/users/:userId", ({ params }) => {
    const { userId } = params;
    const target = adminUsers.find((user) => user.user_id === userId);
    if (!target) {
      return HttpResponse.json({ detail: "User not found" }, { status: 404 });
    }
    const orgIds = target.memberships.map((membership) => membership.org_id);
    adminUsers = adminUsers.filter((user) => user.user_id !== userId);
    superAdmins = superAdmins.filter((admin) => admin.user_id !== userId);
    adminOrgs = adminOrgs
      .filter((org) => org.id !== userId)
      .map((org) =>
        orgIds.includes(org.id)
          ? { ...org, member_count: Math.max(0, org.member_count - 1) }
          : org,
      );
    return HttpResponse.json({ message: "User deleted", user_id: userId });
  }),

  http.post("/api/organizations/provision-user", async ({ request }) => {
    // The server registers this route only when USER_PROVISIONING_ENABLED is set,
    // which the mock config reports as user_provisioning_enabled in mock SaaS.
    // Without it, the path falls through to /api/organizations/{org_id}.
    if (import.meta.env.VITE_MOCK_SAAS !== "true") {
      return HttpResponse.json(
        { detail: "Method Not Allowed" },
        { status: 405 },
      );
    }
    const body = (await request.json()) as {
      email: string;
      password?: string;
      role?: string;
    };
    const orgId = request.headers.get("X-Org-Id") ?? "2";
    if (orgId === MOCK_PERSONAL_ORG_ID) {
      return HttpResponse.json(
        { detail: "Cannot provision users into a personal workspace" },
        { status: 403 },
      );
    }
    const org = adminOrgs.find((row) => row.id === orgId);
    if (!org) {
      return HttpResponse.json(
        { detail: "Target organization not found" },
        { status: 404 },
      );
    }
    const email = body.email.trim().toLowerCase();
    const existing = adminUsers.find(
      (user) => user.email?.toLowerCase() === email,
    );
    if (existing) {
      const already = existing.memberships.some(
        (membership) => membership.org_id === orgId,
      );
      if (!already) {
        existing.memberships.push({
          org_id: orgId,
          org_name: org.name,
          role: body.role ?? "member",
          status: "active",
        });
        org.member_count += 1;
        membershipAddedListeners.forEach((listener) =>
          listener({
            userId: existing.user_id,
            orgId,
            orgName: org.name,
            role: body.role ?? "member",
          }),
        );
      }
      return HttpResponse.json({
        email,
        password: null,
        api_key: "mock-existing-api-key",
        user_id: existing.user_id,
        org_id: orgId,
        role: body.role ?? "member",
        created: false,
        action: already ? "reprovisioned" : "added_to_org",
      });
    }
    const userId = `mock-user-${adminUsers.length + 1}`;
    adminUsers = [
      {
        user_id: userId,
        email,
        name: email.split("@")[0],
        status: "active",
        memberships: [
          {
            org_id: orgId,
            org_name: org.name,
            role: body.role ?? "member",
            status: "active",
          },
        ],
      },
      ...adminUsers,
    ];
    org.member_count += 1;
    return HttpResponse.json(
      {
        email,
        password: body.password || "MockPass1!",
        api_key: "mock-new-api-key",
        user_id: userId,
        org_id: orgId,
        role: body.role ?? "member",
        created: true,
        action: "created",
      },
      { status: 201 },
    );
  }),

  http.post(
    "/api/admin/directory/users/:userId/groups",
    async ({ params, request }) => {
      const { userId } = params;
      const body = (await request.json()) as {
        action?: "suspend" | "resume" | "remove" | "add" | "set_role";
        org_ids?: string[];
        role?: string;
      };
      const target = adminUsers.find((user) => user.user_id === userId);
      if (!target) {
        return HttpResponse.json({ detail: "User not found" }, { status: 404 });
      }
      const orgIds = body.org_ids ?? [];
      if (orgIds.length === 0) {
        return HttpResponse.json(
          { detail: "Select one or more organizations." },
          { status: 400 },
        );
      }

      if (body.action === "suspend" || body.action === "resume") {
        const status = body.action === "suspend" ? "inactive" : "active";
        target.memberships = target.memberships.map((membership) =>
          orgIds.includes(membership.org_id)
            ? { ...membership, status }
            : membership,
        );
        return HttpResponse.json(target);
      }

      if (body.action === "remove") {
        const blocked = orgIds.filter((orgId) => {
          const membership = target.memberships.find(
            (row) => row.org_id === orgId && row.role === "owner",
          );
          if (!membership) {
            return false;
          }
          return !adminUsers.some(
            (user) =>
              user.user_id !== target.user_id &&
              user.memberships.some(
                (row) => row.org_id === orgId && row.role === "owner",
              ),
          );
        });
        if (blocked.length > 0) {
          const names = blocked.map(
            (orgId) => adminOrgs.find((org) => org.id === orgId)?.name ?? orgId,
          );
          return HttpResponse.json(
            {
              detail: `Cannot remove user: last owner of ${names.join(", ")}`,
            },
            { status: 409 },
          );
        }
        target.memberships = target.memberships.filter(
          (membership) => !orgIds.includes(membership.org_id),
        );
        adminOrgs = adminOrgs.map((org) =>
          orgIds.includes(org.id)
            ? { ...org, member_count: Math.max(0, org.member_count - 1) }
            : org,
        );
        return HttpResponse.json(target);
      }

      if (body.action === "set_role") {
        const role = body.role ?? "member";
        const blocked = orgIds.filter((orgId) => {
          const membership = target.memberships.find(
            (row) => row.org_id === orgId && row.role === "owner",
          );
          if (!membership || role === "owner") {
            return false;
          }
          return !adminUsers.some(
            (user) =>
              user.user_id !== target.user_id &&
              user.memberships.some(
                (row) => row.org_id === orgId && row.role === "owner",
              ),
          );
        });
        if (blocked.length > 0) {
          const names = blocked.map(
            (orgId) => adminOrgs.find((org) => org.id === orgId)?.name ?? orgId,
          );
          return HttpResponse.json(
            {
              detail: `Cannot change role: last owner of ${names.join(", ")}`,
            },
            { status: 409 },
          );
        }
        target.memberships = target.memberships.map((membership) =>
          orgIds.includes(membership.org_id)
            ? { ...membership, role }
            : membership,
        );
        return HttpResponse.json(target);
      }

      if (body.action === "add") {
        const role = body.role ?? "member";
        orgIds.forEach((orgId) => {
          const org = adminOrgs.find((row) => row.id === orgId);
          if (!org || org.is_personal) {
            return;
          }
          const existing = target.memberships.find(
            (membership) => membership.org_id === orgId,
          );
          if (existing) {
            if (existing.status !== "active") {
              existing.role = role;
              existing.status = "active";
            }
            return;
          }
          target.memberships.push({
            org_id: orgId,
            org_name: org.name,
            role,
            status: "active",
          });
          org.member_count += 1;
          membershipAddedListeners.forEach((listener) =>
            listener({
              userId: target.user_id,
              orgId,
              orgName: org.name,
              role,
            }),
          );
        });
        target.status = "active";
        return HttpResponse.json(target);
      }

      return HttpResponse.json(
        { detail: "Invalid group action" },
        { status: 400 },
      );
    },
  ),

  http.get("/api/admin/instance-settings", () =>
    HttpResponse.json(instanceSettings),
  ),

  http.patch("/api/admin/instance-settings", async ({ request }) => {
    const body = (await request.json()) as Partial<InstanceSettings>;
    instanceSettings = { ...instanceSettings, ...body };
    return HttpResponse.json(instanceSettings);
  }),

  http.get("/api/admin/setup-state", () =>
    HttpResponse.json(setupStateResponse()),
  ),

  http.patch("/api/admin/setup-state", async ({ request }) => {
    const { wizard_completed: wizardCompleted, ...guide } =
      (await request.json()) as SetupStateUpdate;
    setupState = {
      ...setupState,
      ...guide,
      ...(wizardCompleted === undefined
        ? {}
        : { wizard_pending: !wizardCompleted }),
    };
    return HttpResponse.json(setupStateResponse());
  }),
];
