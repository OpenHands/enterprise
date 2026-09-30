export type SuperAdminOrgStatus = "active" | "suspended" | "removed";

export interface SuperAdminOrgRow {
  id: string;
  name: string;
  members: number;
  status: SuperAdminOrgStatus;
  createdAt: string;
  contactEmail: string;
}

export type SuperAdminOrgRole = "owner" | "admin" | "member";

export interface SuperAdminMembership {
  orgId: string;
  orgName: string;
  role: SuperAdminOrgRole;
}

export interface SuperAdminUserRow {
  id: string;
  name: string;
  email: string;
  memberships: SuperAdminMembership[];
  status: "active" | "invited" | "suspended" | "removed";
}

export interface SuperAdminAdminRow {
  id: string;
  name: string;
  email: string;
  grantedAt: string;
}

export const SUPER_ADMIN_ORGS: SuperAdminOrgRow[] = [
  {
    id: "2",
    name: "Acme Corp",
    members: 24,
    status: "active",
    createdAt: "Jan 12, 2026",
    contactEmail: "me@acme.org",
  },
  {
    id: "4",
    name: "All Hands AI",
    members: 18,
    status: "active",
    createdAt: "Feb 3, 2026",
    contactEmail: "ops@all-hands.dev",
  },
  {
    id: "3",
    name: "Beta LLC",
    members: 7,
    status: "active",
    createdAt: "Mar 21, 2026",
    contactEmail: "admin@beta.llc",
  },
  {
    id: "5",
    name: "Northwind Labs",
    members: 3,
    status: "suspended",
    createdAt: "Apr 8, 2026",
    contactEmail: "it@northwind.example",
  },
];

export const SUPER_ADMIN_USERS: SuperAdminUserRow[] = [
  {
    id: "u-1",
    name: "openhands",
    email: "me@acme.org",
    memberships: [
      { orgId: "2", orgName: "Acme Corp", role: "owner" },
      { orgId: "4", orgName: "All Hands AI", role: "admin" },
    ],
    status: "active",
  },
  {
    id: "u-2",
    name: "Alex Rivera",
    email: "alex@acme.org",
    memberships: [
      { orgId: "2", orgName: "Acme Corp", role: "admin" },
      { orgId: "3", orgName: "Beta LLC", role: "member" },
    ],
    status: "active",
  },
  {
    id: "u-3",
    name: "Jordan Lee",
    email: "jordan@all-hands.dev",
    memberships: [{ orgId: "4", orgName: "All Hands AI", role: "member" }],
    status: "active",
  },
  {
    id: "u-4",
    name: "Sam Patel",
    email: "sam@beta.llc",
    memberships: [
      { orgId: "3", orgName: "Beta LLC", role: "admin" },
      { orgId: "5", orgName: "Northwind Labs", role: "member" },
    ],
    status: "invited",
  },
];

export const SUPER_ADMIN_ADMINS: SuperAdminAdminRow[] = [
  {
    id: "u-1",
    name: "openhands",
    email: "me@acme.org",
    grantedAt: "Jan 2, 2026",
  },
  {
    id: "u-5",
    name: "Riley Chen",
    email: "riley@all-hands.dev",
    grantedAt: "May 14, 2026",
  },
];

export const SUPER_ADMIN_USAGE = {
  conversationsThisMonth: 1482,
  activeUsersThisMonth: 96,
  tokensThisMonth: "42.8M",
  spendThisMonth: "$12,410",
};

export const SUPER_ADMIN_STATS = {
  organizations: SUPER_ADMIN_ORGS.length,
  users: SUPER_ADMIN_USERS.length,
  superAdmins: SUPER_ADMIN_ADMINS.length,
  conversations: SUPER_ADMIN_USAGE.conversationsThisMonth,
};
