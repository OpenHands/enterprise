export type SuperAdminOrgStatus = "active" | "suspended" | "removed";

export interface SuperAdminOrgRow {
  id: string;
  name: string;
  members: number;
  status: SuperAdminOrgStatus;
  createdAt?: string;
  contactEmail: string;
  isPersonal?: boolean;
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
  status: "active" | "invited" | "inactive" | "suspended" | "removed";
}

export interface SuperAdminAdminRow {
  id: string;
  name: string;
  email: string;
  grantedAt?: string;
}

/** Sample rows used only by unit tests for membership rendering. */
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
