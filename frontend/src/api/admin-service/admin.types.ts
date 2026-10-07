/** A single instance user, for the "no organization selected" admin view. */
export interface AdminUser {
  user_id: string;
  email: string | null;
  is_super_admin: boolean;
}

export interface AdminUserPage {
  items: AdminUser[];
  current_page: number;
  per_page: number;
}
