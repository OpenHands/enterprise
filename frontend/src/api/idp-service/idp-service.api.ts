import { openHands } from "../open-hands-axios";
import {
  HasPasswordResponse,
  SetPasswordParams,
  SetPasswordResponse,
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
};
