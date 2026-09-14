import { NativeAuthError } from "#/api/native-auth-service/native-auth-service.api";

const messages = {
  account_link_required: "NATIVE_AUTH$SSO_ACCOUNT_LINK_REQUIRED",
  invitation_required: "NATIVE_AUTH$SSO_INVITATION_REQUIRED",
  email_mismatch: "NATIVE_AUTH$SSO_EMAIL_MISMATCH",
  unavailable: "NATIVE_AUTH$SSO_UNAVAILABLE",
  recent_auth_required: "NATIVE_AUTH$SSO_RECENT_AUTH_REQUIRED",
  temporarily_unavailable: "NATIVE_AUTH$SSO_TEMPORARILY_UNAVAILABLE",
  invalid_response: "NATIVE_AUTH$SSO_FAILED",
} as const;

export function samlErrorMessage(
  error: unknown,
): (typeof messages)[keyof typeof messages] {
  return error instanceof NativeAuthError && error.code
    ? messages[error.code]
    : "NATIVE_AUTH$SSO_FAILED";
}
