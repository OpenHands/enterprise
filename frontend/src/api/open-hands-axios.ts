import axios, { AxiosError, AxiosResponse } from "axios";
import { installAuthentication, getAuthentication } from "./auth-adapter";

export const openHands = axios.create({
  baseURL: `${window.location.protocol}//${import.meta.env.VITE_BACKEND_BASE_URL || window?.location.host}`,
});

installAuthentication(openHands);

// Helper function to check if a response contains an email verification error
const checkForEmailVerificationError = (data: unknown): boolean => {
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
          (msg: unknown) =>
            typeof msg === "string" && msg.includes(EMAIL_NOT_VERIFIED),
        );
      }
    }

    // Search any values in object in case message key is different
    return Object.values(data).some(
      (value) =>
        (typeof value === "string" && value.includes(EMAIL_NOT_VERIFIED)) ||
        (Array.isArray(value) &&
          value.some(
            (v: unknown) =>
              typeof v === "string" && v.includes(EMAIL_NOT_VERIFIED),
          )),
    );
  }

  return false;
};

// Set up the global interceptor
openHands.interceptors.response.use(
  (
    response: AxiosResponse<unknown, unknown>,
  ): AxiosResponse<unknown, unknown> => response,
  (error: AxiosError<unknown, unknown>): Promise<never> => {
    // Check if it's a 403 error with the email verification message
    if (
      getAuthentication().emailVerification &&
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
