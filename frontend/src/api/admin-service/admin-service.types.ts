export interface AdminOrgSummary {
  id: string;
  name: string;
  contact_email: string | null;
  is_default: boolean;
}

export interface AdminOrgListResponse {
  organizations: AdminOrgSummary[];
}

export interface SuperAdminStatusResponse {
  is_super_admin: boolean;
}
