import { openHands } from "../open-hands-axios";
import {
  CreateSignupLinkParams,
  HasPasswordResponse,
  SetPasswordParams,
  SetPasswordResponse,
  SignupLinkResponse,
} from "./idp.types";

/**
 * IDP Service API - Handles the integrated/dev IDP's self-service
 * password endpoints (``/api/idp/password``). Only reachable when the
 * ``enable_integrated_idp`` feature flag is on.
 */
export const idpService = {
  /** Whether the current user already has a password set. */
  hasPassword: async (): Promise<HasPasswordResponse> => {
    const { data } = await openHands.get<HasPasswordResponse>(
      "/api/idp/password",
      { withCredentials: true },
    );
    return data;
  },

  /** Set (first time) or change the current user's password. */
  setPassword: async ({
    currentPassword,
    newPassword,
    confirmPassword,
  }: SetPasswordParams): Promise<SetPasswordResponse> => {
    const { data } = await openHands.post<SetPasswordResponse>(
      "/api/idp/password",
      {
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      },
      { withCredentials: true },
    );
    return data;
  },

  /**
   * Mint a super-admin-only sign-up link (``POST /api/idp/signup-links``).
   * The caller shares the returned URL with the invitee out of band; no
   * password ever passes through this request.
   */
  createSignupLink: async ({
    email,
    role,
    orgId,
  }: CreateSignupLinkParams): Promise<SignupLinkResponse> => {
    const { data } = await openHands.post<SignupLinkResponse>(
      "/api/idp/signup-links",
      { email, role, org_id: orgId },
      { withCredentials: true },
    );
    return data;
  },
};
