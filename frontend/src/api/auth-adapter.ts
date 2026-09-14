import axios, {
  AxiosInstance,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import type { QueryClient } from "@tanstack/react-query";
import { WebClientConfig } from "./option-service/option.types";
import { NativeCsrf } from "./native-csrf";

export type AuthenticationResult =
  | boolean
  | { authenticated: boolean; acceptedTos: boolean };

interface SessionClient {
  nativeSession(): Promise<{ accepted_tos: boolean }>;
  authenticate(appMode: WebClientConfig["app_mode"]): Promise<boolean>;
}

export type AccountAction = "enroll" | "profile" | "manage" | "reset_password";

export interface AuthenticationAdapter {
  readonly accountActions: readonly AccountAction[];
  logout(
    client: AxiosInstance,
    appMode: WebClientConfig["app_mode"],
  ): Promise<void>;
  readonly emailVerification: boolean;
  prepareMutation(
    client: AxiosInstance,
    request: InternalAxiosRequestConfig<unknown>,
  ): Promise<InternalAxiosRequestConfig<unknown>>;
  receivedResponse(response: AxiosResponse<unknown, unknown>): void;
  retryRejectedMutation(
    client: AxiosInstance,
    error: unknown,
  ): Promise<AxiosResponse<unknown, unknown>>;
  authenticate(
    client: SessionClient,
    appMode: WebClientConfig["app_mode"],
  ): Promise<AuthenticationResult>;
  sessionQueryKey(
    appMode: WebClientConfig["app_mode"] | undefined,
  ): readonly string[];
  checkSessionOnPage(intermediate: boolean): boolean;
  providerLoginEnabled(
    appMode: WebClientConfig["app_mode"] | undefined | null,
  ): boolean;
  redirectToLogin(intermediate: boolean, storedLoginMethod: boolean): boolean;
  showSessionExpired(storedLoginMethod: boolean): boolean;
  acceptedTos(client: QueryClient): Promise<void>;
  reset(): void;
}

async function logout(
  client: AxiosInstance,
  appMode: WebClientConfig["app_mode"],
): Promise<void> {
  await client.post<unknown>(
    appMode === "saas" ? "/api/logout" : "/api/unset-provider-tokens",
  );
}

const keycloak: AuthenticationAdapter = Object.freeze<AuthenticationAdapter>({
  emailVerification: true,
  logout,
  accountActions: [],
  async prepareMutation(
    _client: AxiosInstance,
    request: InternalAxiosRequestConfig<unknown>,
  ): Promise<InternalAxiosRequestConfig<unknown>> {
    return request;
  },
  receivedResponse(): void {
    /* Keycloak cookies rotate on the server. */
  },
  async retryRejectedMutation(
    _client: AxiosInstance,
    error: unknown,
  ): Promise<AxiosResponse<unknown, unknown>> {
    throw error;
  },
  async authenticate(
    client: SessionClient,
    appMode: WebClientConfig["app_mode"],
  ): Promise<AuthenticationResult> {
    return client.authenticate(appMode);
  },
  sessionQueryKey(
    appMode: WebClientConfig["app_mode"] | undefined,
  ): readonly string[] {
    return ["user", "authenticated", appMode || ""];
  },
  checkSessionOnPage(intermediate: boolean): boolean {
    return !intermediate;
  },
  providerLoginEnabled(
    appMode: WebClientConfig["app_mode"] | undefined | null,
  ): boolean {
    return appMode === "saas";
  },
  redirectToLogin(intermediate: boolean, storedLoginMethod: boolean): boolean {
    return !intermediate && !storedLoginMethod;
  },
  showSessionExpired(storedLoginMethod: boolean): boolean {
    return storedLoginMethod;
  },
  async acceptedTos(): Promise<void> {
    /* Consent is carried by the refreshed signed cookie. */
  },
  reset(): void {
    /* No browser proof is retained by this transport. */
  },
});

const csrf = new NativeCsrf();
const openhands: AuthenticationAdapter = Object.freeze<AuthenticationAdapter>({
  emailVerification: false,
  logout,
  accountActions: ["enroll", "profile", "manage", "reset_password"],
  prepareMutation(
    client: AxiosInstance,
    request: InternalAxiosRequestConfig<unknown>,
  ): Promise<InternalAxiosRequestConfig<unknown>> {
    return csrf.prepareMutation(client, request);
  },
  receivedResponse(response: AxiosResponse<unknown, unknown>): void {
    csrf.receivedResponse(response);
  },
  retryRejectedMutation(
    client: AxiosInstance,
    error: unknown,
  ): Promise<AxiosResponse<unknown, unknown>> {
    return csrf.retryRejectedMutation(client, error);
  },
  async authenticate(client: SessionClient): Promise<AuthenticationResult> {
    const session = await client.nativeSession();
    return { authenticated: true, acceptedTos: session.accepted_tos };
  },
  sessionQueryKey(
    appMode: WebClientConfig["app_mode"] | undefined,
  ): readonly string[] {
    return ["user", "authenticated", appMode || "", "native"];
  },
  checkSessionOnPage(): boolean {
    return true;
  },
  providerLoginEnabled(): boolean {
    return false;
  },
  redirectToLogin(): boolean {
    return true;
  },
  showSessionExpired(): boolean {
    return false;
  },
  async acceptedTos(client: QueryClient): Promise<void> {
    client.setQueryData(openhands.sessionQueryKey("saas"), {
      authenticated: true,
      acceptedTos: true,
    });
    await client.invalidateQueries({ queryKey: ["user", "authenticated"] });
  },
  reset(): void {
    csrf.reset();
  },
});

let selected: AuthenticationAdapter | undefined;

export function selectAuthentication(
  mode?: WebClientConfig["auth_mode"],
): AuthenticationAdapter {
  return mode === "native" ? openhands : keycloak;
}

export function configureAuthentication(
  mode?: WebClientConfig["auth_mode"],
): void {
  const next = selectAuthentication(mode);
  if (selected !== next) {
    selected?.reset();
    next.reset();
  }
  selected = next;
}

export function getAuthentication(): AuthenticationAdapter {
  return selected || keycloak;
}

export function isApplicationRequest(
  client: AxiosInstance,
  request: InternalAxiosRequestConfig<unknown>,
): boolean {
  const base = new URL(client.defaults.baseURL || window.location.origin);
  const target = new URL(request.url || "", request.baseURL || base);
  return target.origin === base.origin;
}

export function installAuthentication(client: AxiosInstance): void {
  let pendingConfig: Promise<void> | undefined;
  client.interceptors.request.use(
    async (
      request: InternalAxiosRequestConfig<unknown>,
    ): Promise<InternalAxiosRequestConfig<unknown>> => {
      if (
        !isApplicationRequest(client, request) ||
        ["get", "head", "options"].includes(request.method || "get")
      )
        return request;
      if (!selected) {
        pendingConfig ??= client
          .get<WebClientConfig>("/api/v1/web-client/config")
          .then(({ data }: AxiosResponse<WebClientConfig, unknown>): void =>
            configureAuthentication(data.auth_mode),
          )
          .finally((): void => {
            pendingConfig = undefined;
          });
        await pendingConfig;
      }
      return getAuthentication().prepareMutation(client, request);
    },
  );
  client.interceptors.response.use(
    (
      response: AxiosResponse<unknown, unknown>,
    ): AxiosResponse<unknown, unknown> => {
      getAuthentication().receivedResponse(response);
      return response;
    },
    async (error: unknown): Promise<AxiosResponse<unknown, unknown>> => {
      if (
        !axios.isAxiosError<unknown, unknown>(error) ||
        !error.config ||
        !isApplicationRequest(client, error.config)
      )
        throw error;
      return getAuthentication().retryRejectedMutation(client, error);
    },
  );
}
