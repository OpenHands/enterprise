export interface HasPasswordResponse {
  has_password: boolean;
}

export interface SetPasswordParams {
  /** Required only when the user already has a password set. */
  currentPassword?: string;
  newPassword: string;
  confirmPassword: string;
}

export interface SetPasswordResponse {
  message: string;
}

/**
 * Role an admin-issued sign-up link (``POST /api/idp/signup-links``) grants
 * on accept: an org-scoped role (requires ``orgId``) or the instance-level
 * ``superadmin`` (never combined with ``orgId``). Deliberately not
 * ``OrganizationUserRole`` -- that type is org-membership-only, while
 * ``superadmin`` is a separate, instance-wide concept (see
 * ``server.routes.super_admins``).
 */
export type SignupLinkRole = "member" | "admin" | "owner" | "superadmin";

export interface CreateSignupLinkParams {
  email: string;
  role: SignupLinkRole;
  /** Required unless ``role`` is ``"superadmin"``. */
  orgId?: string;
}

export interface SignupLinkResponse {
  url: string;
  expires_at: string;
  role: SignupLinkRole;
}
