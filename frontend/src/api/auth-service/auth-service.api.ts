import { openHands } from "../open-hands-axios";
import {
  AuthenticateResponse,
  GitHubAccessTokenResponse,
  AuthCapabilities,
  PasswordLogin,
  PasswordChange,
  AuthDestination,
  AuthResult,
  CreateAccount,
  CreatedAccount,
} from "./auth.types";
import { WebClientConfig } from "../option-service/option.types";
import { authPost } from "./csrf";

/**
 * Authentication service for handling all authentication-related API calls
 */
class AuthService {
  /**
   * Authenticate with GitHub token
   * @param appMode The application mode (saas or oss)
   * @returns Response with authentication status and user info if successful
   */
  static async authenticate(
    appMode: WebClientConfig["app_mode"],
  ): Promise<boolean> {
    if (appMode === "oss") return true;

    // Just make the request, if it succeeds (no exception thrown), return true
    await authPost<AuthenticateResponse>("/api/authenticate");
    return true;
  }

  /**
   * Get GitHub access token from Keycloak callback
   * @param code Code provided by GitHub
   * @returns GitHub access token
   */
  static async getGitHubAccessToken(
    code: string,
  ): Promise<GitHubAccessTokenResponse> {
    const { data } = await openHands.post<GitHubAccessTokenResponse>(
      "/api/keycloak/callback",
      {
        code,
      },
    );
    return data;
  }

  /**
   * Logout user from the application
   * @param appMode The application mode (saas or oss)
   */
  static async logout(appMode: WebClientConfig["app_mode"]): Promise<void> {
    const endpoint =
      appMode === "saas" ? "/api/logout" : "/api/unset-provider-tokens";
    if (appMode === "saas") await authPost(endpoint);
    else await openHands.post(endpoint);
  }

  static async getCapabilities(): Promise<AuthCapabilities> {
    const { data } = await openHands.get<AuthCapabilities>(
      "/api/auth/capabilities",
    );
    return data;
  }

  static async login(body: PasswordLogin) {
    const { data } = await authPost<AuthResult>("/api/auth/login", body);
    return data;
  }

  static async changePassword(body: PasswordChange) {
    const { data } = await authPost<AuthResult>(
      "/api/auth/password/change",
      body,
    );
    return data;
  }

  static async forgotPassword(body: AuthDestination & { email: string }) {
    await authPost("/api/auth/password/forgot", body);
  }

  static async resetPassword(
    body: AuthDestination & { token: string; new_password: string },
  ) {
    const { data } = await authPost<AuthResult>(
      "/api/auth/password/reset",
      body,
    );
    return data;
  }

  static async enroll(
    body: AuthDestination & { invitation_token: string; password: string },
  ) {
    const { data } = await authPost<AuthResult>(
      "/api/auth/invitations/enroll",
      body,
    );
    return data;
  }

  static async requestVerification(body: AuthDestination) {
    await authPost("/api/auth/email/request-verification", body);
  }

  static async verifyEmail(body: AuthDestination & { token: string }) {
    const { data } = await authPost<AuthResult>("/api/auth/email/verify", body);
    return data;
  }

  static async changeEmail(body: AuthDestination & { email: string }) {
    await authPost("/api/auth/email/change", body);
  }

  static async createAccount(body: CreateAccount) {
    const { data } = await authPost<CreatedAccount>("/api/auth/accounts", body);
    return data;
  }
}

export default AuthService;
