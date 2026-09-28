import { openHands } from "../open-hands-axios";
import { AuthenticateResponse, GitHubAccessTokenResponse } from "./auth.types";
import { WebClientConfig } from "../option-service/option.types";

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
    await openHands.post<AuthenticateResponse>("/api/authenticate");
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
    await openHands.post(endpoint);
  }

  /**
   * Log in via the development IDP (email-only, no password).
   *
   * Only available on self-hosted deployments with no real IDP configured.
   * Returns a redirect URL the browser should navigate to.
   * @param email The user's email address
   * @param redirectUrl Optional URL to redirect to after login (defaults to "/")
   * @returns The redirect URL to navigate to after login
   */
  static async devIdpLogin(
    email: string,
    redirectUrl?: string,
  ): Promise<{ redirect_url: string }> {
    const { data } = await openHands.post<{ redirect_url: string }>(
      "/api/dev-idp/login",
      { email, redirect_url: redirectUrl ?? "/" },
    );
    return data;
  }
}

export default AuthService;
