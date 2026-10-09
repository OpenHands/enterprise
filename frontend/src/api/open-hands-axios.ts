import axios, { AxiosError, AxiosResponse } from "axios";
import { getSelectedOrganizationIdFromStore } from "#/stores/selected-organization-store";
import {
  OrganizationSuspensionReason,
  useSuspendedOrganizationStore,
} from "#/stores/suspended-organization-store";

export const openHands = axios.create({
  baseURL: `${window.location.protocol}//${import.meta.env.VITE_BACKEND_BASE_URL || window?.location.host}`,
});

// Helper function to check if a response contains an email verification error
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const checkForEmailVerificationError = (data: any): boolean => {
  const EMAIL_NOT_VERIFIED = "EmailNotVerifiedError";

  if (typeof data === "string") {
    return data.includes(EMAIL_NOT_VERIFIED);
  }

  if (typeof data === "object" && data !== null) {
    if ("message" in data) {
      const { message } = data;
      if (typeof message === "string") {
        return message.includes(EMAIL_NOT_VERIFIED);
      }
      if (Array.isArray(message)) {
        return message.some(
          (msg) => typeof msg === "string" && msg.includes(EMAIL_NOT_VERIFIED),
        );
      }
    }

    // Search any values in object in case message key is different
    return Object.values(data).some(
      (value) =>
        (typeof value === "string" && value.includes(EMAIL_NOT_VERIFIED)) ||
        (Array.isArray(value) &&
          value.some(
            (v) => typeof v === "string" && v.includes(EMAIL_NOT_VERIFIED),
          )),
    );
  }

  return false;
};

// The server's exact `detail` for a request made in a suspended organization
// or with a suspended membership (server/auth/org_access.py).
const SUSPENSION_REASONS: Record<string, OrganizationSuspensionReason> = {
  "Organization is suspended": "organization",
  "User membership is suspended": "membership",
};

// Set up the global interceptor
openHands.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    // Check if it's a 403 error with the email verification message
    if (
      error.response?.status === 403 &&
      checkForEmailVerificationError(error.response?.data)
    ) {
      if (window.location.pathname !== "/settings/user") {
        window.location.reload();
      }
    }

    // Block the selected organization when the server refuses to use it. A
    // failed switch leaves the selection unchanged, so it stays a toast.
    const detail = (error.response?.data as { detail?: unknown } | undefined)
      ?.detail;
    const suspensionReason =
      typeof detail === "string" ? SUSPENSION_REASONS[detail] : undefined;
    const selectedOrgId = getSelectedOrganizationIdFromStore();
    if (
      error.response?.status === 403 &&
      suspensionReason &&
      selectedOrgId &&
      !error.config?.url?.endsWith("/switch")
    ) {
      useSuspendedOrganizationStore.getState().setSuspension({
        orgId: selectedOrgId,
        reason: suspensionReason,
      });
    }

    // Continue with the error for other error handlers
    return Promise.reject(error);
  },
);
