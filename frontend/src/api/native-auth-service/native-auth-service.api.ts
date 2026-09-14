import axios from "axios";
import AuthService from "../auth-service/auth-service.api";
import { openHands } from "../open-hands-axios";

const SAML_ERROR_CODES = [
  "account_link_required",
  "invitation_required",
  "email_mismatch",
  "unavailable",
  "recent_auth_required",
  "temporarily_unavailable",
  "invalid_response",
] as const;
export type SamlErrorCode = (typeof SAML_ERROR_CODES)[number];

export class NativeAuthError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: SamlErrorCode,
  ) {
    super(message);
  }
}

// Never retain Axios errors: request config can contain passwords or link tokens.
export function safeNativeError(error: unknown): NativeAuthError {
  if (error instanceof NativeAuthError) return error;
  if (axios.isAxiosError<unknown, unknown>(error)) {
    const data = error.response?.data;
    const detail =
      typeof data === "object" && data !== null && "detail" in data
        ? data.detail
        : undefined;
    return new NativeAuthError(
      error.response?.status || 0,
      typeof detail === "string"
        ? detail
        : "The request could not be completed. Please try again.",
    );
  }
  return new NativeAuthError(
    0,
    "The request could not be completed. Please try again.",
  );
}

export type NativeAuthenticationMethod = "password" | "saml";

export interface NativeProfile {
  id: string;
  email?: string;
  global_permissions: string[];
  has_password?: boolean;
  authentication_methods?: NativeAuthenticationMethod[];
}
export interface Enrollment {
  email: string;
  org_id: string | null;
  org_name: string | null;
  org_role_id: number | null;
  expires_at: string;
  action: "set_password" | "login";
  authentication_methods?: NativeAuthenticationMethod[];
}
export interface NativeInvitation {
  id: string;
  email: string;
  org_id: string | null;
  org_role_id: number | null;
  expires_at: string;
  created_at: string;
  consumed_at?: string | null;
  revoked_at?: string | null;
}
export interface NativeAccount {
  id: string;
  email: string | null;
  state:
    | "profile_present"
    | "reonboardable"
    | "profile_absent_blocked"
    | "deleted";
  profile_present: boolean;
  is_disabled: boolean;
  role_id: number | null;
  created_at: string;
  pending_invitations: NativeInvitation[];
  authentication_methods?: NativeAuthenticationMethod[];
}
export interface NativeInvitationOrganization {
  id: string;
  name: string;
}
export interface AccountInvitationInput {
  email: string;
  org_id?: string;
  org_role_id?: number;
}
export interface AccountLink {
  invite_url?: string;
  reset_url?: string;
  expires_at: string;
}
export interface NativeLoginInput {
  email: string;
  password: string;
  return_path?: string;
  invitation_token?: string;
}

export interface SamlStartInput {
  return_path?: string;
  invitation_token?: string;
  link?: boolean;
  reauthenticate?: boolean;
}

// Protocol failures must not retain assertion details or the redirect URL.
async function samlRequest(
  path: string,
  input: SamlStartInput,
): Promise<AuthRedirect> {
  try {
    return (await openHands.post<{ redirect_to: string }>(path, input)).data;
  } catch (error) {
    const response = axios.isAxiosError<unknown, unknown>(error)
      ? error.response
      : undefined;
    const data = response?.data;
    const code =
      typeof data === "object" && data !== null && "code" in data
        ? data.code
        : undefined;
    throw new NativeAuthError(
      response?.status || 0,
      "Single sign-on could not be completed. Please try again.",
      SAML_ERROR_CODES.find(
        (candidate: SamlErrorCode): boolean => candidate === code,
      ),
    );
  }
}

export interface AuthRedirect {
  redirect_to: string;
}
export interface NativeSession {
  accepted_tos: boolean;
}
export type NativeLoginResponse = AuthRedirect & NativeSession;
export type EnrollmentResponse =
  | { action: "login"; redirect_to?: string }
  | ({ action: "complete"; redirect_to?: string } & NativeSession);
export interface PaginatedResponse<Item> {
  items: Item[];
  total: number;
}
export interface NativeRole {
  id: number;
  name: string;
}
export interface NativeSuperadmin {
  user_id: string;
  email: string;
}
export interface NativeSuperadmins {
  super_admins: NativeSuperadmin[];
}
export interface AccountLifecycleResult {
  warnings: string[];
}

export const NativeAuthService = {
  profile: async (): Promise<NativeProfile> =>
    (await openHands.get<NativeProfile>("/api/v1/users/me")).data,
  startSaml: (input: SamlStartInput): Promise<AuthRedirect> =>
    samlRequest("/api/auth/saml/start", input),
  completeSaml: async (): Promise<NativeLoginResponse> => {
    const result = await samlRequest("/api/auth/saml/complete", {});
    return { ...result, ...(await AuthService.nativeSession()) };
  },
  login: async (input: NativeLoginInput): Promise<NativeLoginResponse> => {
    const { data } = await openHands.post<{ redirect_to: string }>(
      "/api/auth/password/login",
      input,
    );
    if (input.invitation_token) {
      await openHands.post("/api/auth/enrollment/accept-membership", {
        token: input.invitation_token,
      });
    }
    return { ...data, ...(await AuthService.nativeSession()) };
  },
  inspect: async (token: string): Promise<Enrollment> =>
    (
      await openHands.post<Enrollment>("/api/auth/enrollment/inspect", {
        token,
      })
    ).data,
  enroll: async (input: {
    token: string;
    password: string;
  }): Promise<EnrollmentResponse> => {
    const { data } = await openHands.post<{
      action: "complete" | "login";
      redirect_to?: string;
    }>("/api/auth/enrollment/complete", input);
    if (data.action === "login") return { ...data, action: "login" };
    return {
      ...data,
      action: "complete",
      ...(await AuthService.nativeSession()),
    };
  },
  resetPassword: async (input: {
    token: string;
    new_password: string;
  }): Promise<void> => {
    await openHands.post("/api/auth/password/reset/complete", input);
  },
  changePassword: async (input: {
    current_password: string;
    new_password: string;
  }): Promise<void> => {
    await openHands.post("/api/auth/password/change", input);
  },
  accounts: async (offset: number): Promise<PaginatedResponse<NativeAccount>> =>
    (
      await openHands.get<{ items: NativeAccount[]; total: number }>(
        "/api/admin/auth-accounts",
        { params: { offset, limit: 50 } },
      )
    ).data,
  account: async (id: string): Promise<NativeAccount> =>
    (await openHands.get<NativeAccount>(`/api/admin/auth-accounts/${id}`)).data,
  invitations: async (
    offset: number,
  ): Promise<PaginatedResponse<NativeInvitation>> =>
    (
      await openHands.get<{ items: NativeInvitation[]; total: number }>(
        "/api/admin/auth-invitations",
        { params: { offset, limit: 50 } },
      )
    ).data,
  invite: async (input: AccountInvitationInput): Promise<AccountLink> =>
    (await openHands.post<AccountLink>("/api/admin/auth-invitations", input))
      .data,
  reissue: async (id: string): Promise<AccountLink> =>
    (
      await openHands.post<AccountLink>(
        `/api/admin/auth-invitations/${id}/reissue`,
      )
    ).data,
  revokeInvitation: async (id: string): Promise<void> => {
    await openHands.delete(`/api/admin/auth-invitations/${id}`);
  },
  issueReset: async (id: string): Promise<AccountLink> =>
    (
      await openHands.post<AccountLink>(
        `/api/admin/auth-accounts/${id}/password-reset`,
      )
    ).data,
  lifecycle: async ({
    id,
    action,
  }: {
    id: string;
    action: "enable" | "disable" | "delete";
  }): Promise<AccountLifecycleResult> => {
    const path = `/api/admin/users/${id}`;
    if (action === "delete")
      return (await openHands.delete<{ warnings: string[] }>(path)).data;
    return (await openHands.post<{ warnings: string[] }>(`${path}/${action}`))
      .data;
  },
  roles: async (): Promise<NativeRole[]> =>
    (
      await openHands.get<{ id: number; name: string }[]>(
        "/api/admin/auth-roles",
      )
    ).data,
  invitationOrganizations: async (
    offset: number,
  ): Promise<PaginatedResponse<NativeInvitationOrganization>> =>
    (
      await openHands.get<{
        items: NativeInvitationOrganization[];
        total: number;
      }>("/api/admin/auth-organizations", { params: { offset, limit: 100 } })
    ).data,
  superadmins: async (): Promise<NativeSuperadmins> =>
    (
      await openHands.get<{
        super_admins: { user_id: string; email: string }[];
      }>("/api/admin/super-admins")
    ).data,
  setSuperadmin: async ({
    id,
    enabled,
  }: {
    id: string;
    enabled: boolean;
  }): Promise<void> => {
    if (enabled)
      await openHands.post("/api/admin/super-admins", { user_id: id });
    else await openHands.delete(`/api/admin/super-admins/${id}`);
  },
};
