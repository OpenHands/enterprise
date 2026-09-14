import axios, { AxiosResponse } from "axios";
import { openHands } from "../open-hands-axios";
import { AuthenticateResponse, GitHubAccessTokenResponse } from "./auth.types";
import { WebClientConfig } from "../option-service/option.types";

/**
 * Authentication service for handling all authentication-related API calls
 */
class AuthService {
  static async nativeSession(): Promise<{ accepted_tos: boolean }> {
    return (
      await openHands.post<{ accepted_tos: boolean }>("/api/authenticate")
    ).data;
  }

  static async verifyDevice(code: string): Promise<boolean> {
    try {
      await openHands.post(
        "/oauth/device/verify-authenticated",
        new URLSearchParams({ user_code: code }),
        {
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          withCredentials: true,
        },
      );
      return true;
    } catch (error) {
      if (axios.isAxiosError<unknown, unknown>(error) && error.response)
        return false;
      throw error;
    }
  }

  static acceptTos(
    redirectUrl: string,
  ): Promise<
    AxiosResponse<{ redirect_url?: string }, { redirect_url: string }>
  > {
    return openHands.post<
      { redirect_url?: string },
      AxiosResponse<{ redirect_url?: string }, { redirect_url: string }>,
      { redirect_url: string }
    >("/api/accept_tos", {
      redirect_url: redirectUrl,
    });
  }

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
}

export default AuthService;
