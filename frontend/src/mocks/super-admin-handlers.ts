import { http, HttpResponse } from "msw";
import { requestWantsFreshSa } from "./mock-fresh-sa";

const MOCK_SUPER_ADMINS = [
  { user_id: "99", email: "me@acme.org" },
  { user_id: "u-5", email: "riley@all-hands.dev" },
];

let superAdmins = [...MOCK_SUPER_ADMINS];

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

const FRESH_SA_ADMIN_ORG = {
  id: "2",
  name: "Acme Corp",
  contact_email: "me@acme.org",
  contact_name: "openhands",
  member_count: 1,
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
  adminOrgs = [{ ...FRESH_SA_ADMIN_ORG }];
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

export const resetSuperAdminMockState = () => {
  superAdmins = [...MOCK_SUPER_ADMINS];
  adminOrgs = [...MOCK_ADMIN_ORGS];
  adminUsers = [...MOCK_ADMIN_USERS];
  freshSaAdminApplied = false;
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

  http.get("/api/admin/users", ({ request }) => {
    ensureFreshSaAdminState(request);
    return HttpResponse.json({ users: adminUsers });
  }),

  http.patch("/api/admin/users/:userId", async ({ params, request }) => {
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
    target.memberships = target.memberships.map((membership) => ({
      ...membership,
      status: body.status!,
    }));
    return HttpResponse.json(target);
  }),

  http.delete("/api/admin/users/:userId", ({ params }) => {
    const { userId } = params;
    const target = adminUsers.find((user) => user.user_id === userId);
    if (!target) {
      return HttpResponse.json({ detail: "User not found" }, { status: 404 });
    }
    const removed = target.memberships
      .filter((membership) => membership.org_id !== userId)
      .map((membership) => membership.org_id);
    target.memberships = target.memberships.filter(
      (membership) => membership.org_id === userId,
    );
    return HttpResponse.json({
      message: "User removed from team organizations",
      user_id: userId,
      removed_org_ids: removed,
    });
  }),

  http.post("/api/organizations/provision-user", async ({ request }) => {
    const body = (await request.json()) as {
      email: string;
      role?: string;
    };
    const orgId = request.headers.get("X-Org-Id") ?? "2";
    const org = adminOrgs.find((row) => row.id === orgId);
    const email = body.email.trim().toLowerCase();
    const existing = adminUsers.find(
      (user) => user.email?.toLowerCase() === email,
    );
    if (existing) {
      return HttpResponse.json({
        email,
        password: null,
        api_key: "mock-existing-api-key",
        user_id: existing.user_id,
        org_id: orgId,
        role: body.role ?? "member",
        created: false,
        action: "reprovisioned",
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
            org_name: org?.name ?? "Organization",
            role: body.role ?? "member",
            status: "active",
          },
        ],
      },
      ...adminUsers,
    ];
    return HttpResponse.json(
      {
        email,
        password: "MockPass1!",
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
];
