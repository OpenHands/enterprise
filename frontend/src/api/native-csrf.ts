import axios, {
  AxiosInstance,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { WebClientConfig } from "./option-service/option.types";

let nativeMode: boolean | undefined;
let proof: string | undefined;
let generation = 0;
let pendingProof: Promise<string> | undefined;

export const isNativeAuth = (): boolean => nativeMode === true;

export function resetNativeCsrf(): void {
  generation += 1;
  proof = undefined;
  pendingProof = undefined;
}

export function configureNativeAuth(mode?: "native" | "keycloak"): void {
  const next = mode === "native";
  if (nativeMode !== next) resetNativeCsrf();
  nativeMode = next;
}

type CsrfRequest = InternalAxiosRequestConfig<unknown> & {
  nativeCsrfRetried: boolean;
};

export function installNativeCsrf(client: AxiosInstance): void {
  let pendingConfig: Promise<void> | undefined;
  const getProof = (): Promise<string> => {
    if (proof) return Promise.resolve(proof);
    if (pendingProof) return pendingProof;
    const currentGeneration = generation;
    pendingProof = client
      .get<{ csrf_token: string }>("/api/auth/csrf", { withCredentials: true })
      .then(
        ({
          data,
        }: AxiosResponse<{ csrf_token: string }, unknown>):
          | string
          | Promise<string> => {
          // A login/logout may have rotated the cookie while this GET was in flight.
          if (generation !== currentGeneration) return getProof();
          proof = data.csrf_token;
          return proof;
        },
      )
      .finally((): void => {
        if (generation === currentGeneration) pendingProof = undefined;
      });
    return pendingProof;
  };

  const isApplicationRequest = (
    request: InternalAxiosRequestConfig<unknown>,
  ): boolean => {
    const base = new URL(client.defaults.baseURL || window.location.origin);
    const target = new URL(request.url || "", request.baseURL || base);
    return target.origin === base.origin;
  };

  client.interceptors.request.use(
    async (
      request: InternalAxiosRequestConfig<unknown>,
    ): Promise<InternalAxiosRequestConfig<unknown>> => {
      if (!isApplicationRequest(request)) return request;
      if (["get", "head", "options"].includes(request.method || "get"))
        return request;
      // Direct entry to the TOS page can mutate before its normal config query.
      if (nativeMode === undefined) {
        pendingConfig ??= client
          .get<WebClientConfig>("/api/v1/web-client/config")
          .then(({ data }: AxiosResponse<WebClientConfig, unknown>): void => {
            configureNativeAuth(data.auth_mode);
          })
          .finally((): void => {
            pendingConfig = undefined;
          });
        await pendingConfig;
      }
      if (!isNativeAuth()) return request;
      const { headers } = request;
      headers.set("X-CSRF-Token", await getProof());
      return { ...request, headers, withCredentials: true };
    },
  );

  client.interceptors.response.use(
    (
      response: AxiosResponse<unknown, unknown>,
    ): AxiosResponse<unknown, unknown> => {
      if (
        isNativeAuth() &&
        /\/api\/(auth\/password\/(login|change)|auth\/enrollment\/complete|auth\/saml\/complete|logout)$/.test(
          response.config.url || "",
        )
      ) {
        resetNativeCsrf();
      }
      return response;
    },
    async (error: unknown): Promise<AxiosResponse<unknown, unknown>> => {
      if (!axios.isAxiosError<unknown, unknown>(error)) throw error;
      const request = error.config;
      const data = error.response?.data;
      const invalidProof =
        typeof data === "object" &&
        data !== null &&
        "detail" in data &&
        data.detail === "Invalid CSRF token";
      // Only this precise rejection guarantees the operation was not applied.
      // A stale proof from another tab or expired cookie gets one refresh.
      if (
        isNativeAuth() &&
        request &&
        isApplicationRequest(request) &&
        !(
          "nativeCsrfRetried" in request && request.nativeCsrfRetried === true
        ) &&
        error.response?.status === 403 &&
        invalidProof
      ) {
        resetNativeCsrf();
        const retry: CsrfRequest = { ...request, nativeCsrfRetried: true };
        return client.request<
          unknown,
          AxiosResponse<unknown, unknown>,
          unknown
        >(retry);
      }
      return Promise.reject(error);
    },
  );
}
