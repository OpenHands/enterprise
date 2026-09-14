import axios from "axios";
import AuthService from "../auth-service/auth-service.api";
import { openHands } from "../open-hands-axios";

export class NativeAuthError extends Error {
  constructor(
    public status: number,
    message: string,
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

export type NativeAuthenticationMethod = "password";

export interface NativeLoginInput {
  email: string;
  password: string;
  return_path?: string;
}

export interface AuthRedirect {
  redirect_to: string;
}
export interface NativeSession {
  accepted_tos: boolean;
}
export type NativeLoginResponse = AuthRedirect & NativeSession;
export const NativeAuthService = {
  login: async (input: NativeLoginInput): Promise<NativeLoginResponse> => {
    const { data } = await openHands.post<{ redirect_to: string }>(
      "/api/auth/password/login",
      input,
    );
    return { ...data, ...(await AuthService.nativeSession()) };
  },
};
