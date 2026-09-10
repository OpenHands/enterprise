import axios, { AxiosError, AxiosResponse } from "axios";
import {
  browserCsrfEnabled,
  configureBrowserCsrf,
  ensureCsrfSeed,
} from "./auth-service/browser-csrf";
import { authPageUrl, safeAuthRedirect } from "#/utils/auth-redirect";

export const openHands = axios.create({
  baseURL: `${window.location.protocol}//${import.meta.env.VITE_BACKEND_BASE_URL || window?.location.host}`,
  withCredentials: true,
  xsrfCookieName: "oh_csrf",
  xsrfHeaderName: "X-CSRF-Token",
});

openHands.interceptors.request.use(async (config) => {
  const method = config.method?.toLowerCase() || "get";
  if (
    browserCsrfEnabled() &&
    !["get", "head", "options"].includes(method) &&
    !config.headers.has("X-CSRF-Token") &&
    !config.headers.has("Authorization") &&
    !config.headers.has("X-Session-API-Key") &&
    !config.headers.has("X-Access-Token")
  ) {
    config.headers.set("X-CSRF-Token", await ensureCsrfSeed(openHands));
  }
  return config;
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

// Set up the global interceptor
openHands.interceptors.response.use(
  (response: AxiosResponse) => {
    if (response.config.url === "/api/v1/web-client/config") {
      configureBrowserCsrf(response.data?.app_mode === "saas");
    } else if (response.config.url === "/api/auth/capabilities") {
      configureBrowserCsrf(true);
    }
    return response;
  },
  (error: AxiosError) => {
    const data = error.response?.data as
      | { detail?: { code?: string; redirect_url?: string } }
      | undefined;
    if (
      error.response?.status === 403 &&
      data?.detail?.code === "password_change_required" &&
      window.location.pathname !== "/auth/change-password"
    ) {
      const returnTo = `${window.location.pathname}${window.location.search}`;
      window.location.href = data.detail.redirect_url
        ? safeAuthRedirect(data.detail.redirect_url)
        : authPageUrl("/auth/change-password", returnTo);
    }
    // Check if it's a 403 error with the email verification message
    if (
      error.response?.status === 403 &&
      checkForEmailVerificationError(error.response?.data)
    ) {
      if (window.location.pathname !== "/settings/user") {
        window.location.reload();
      }
    }

    // Continue with the error for other error handlers
    return Promise.reject(error);
  },
);
