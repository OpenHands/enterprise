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
  /** Membership row status from the admin API. Inactive means suspended. */
  status?: string | null;
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
