import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";

export const AUTH_PROVIDERS_QUERY_KEY = ["super-admin", "auth-providers"];

export const useAuthProviders = () =>
  useQuery({
    queryKey: AUTH_PROVIDERS_QUERY_KEY,
    queryFn: () => authProvidersService.listAuthProviders(),
  });

export const authProviderQueryKey = (providerId: number) => [
  ...AUTH_PROVIDERS_QUERY_KEY,
  providerId,
];

/** A single provider -- backs ``/super-admin/auth-providers/{provider_id}``. */
export const useAuthProvider = (providerId: number) =>
  useQuery({
    queryKey: authProviderQueryKey(providerId),
    queryFn: () => authProvidersService.getAuthProvider(providerId),
    enabled: Number.isFinite(providerId),
  });

export const authProviderUsersQueryKey = (
  providerId: number,
  page: number,
  limit: number,
  q: string | undefined,
) => [...AUTH_PROVIDERS_QUERY_KEY, providerId, "users", page, limit, q];

/** Paginated "User Links" for one provider -- backs
 * ``/super-admin/auth-providers/{provider_id}/users``. */
export const useAuthProviderUsers = ({
  providerId,
  page = 1,
  limit = 10,
  q,
}: {
  providerId: number;
  page?: number;
  limit?: number;
  q?: string;
}) =>
  useQuery({
    queryKey: authProviderUsersQueryKey(providerId, page, limit, q),
    queryFn: () =>
      authProvidersService.listAuthProviderUsers({
        providerId,
        page,
        limit,
        q,
      }),
    enabled: Number.isFinite(providerId),
    placeholderData: keepPreviousData,
  });

export const PUBLIC_IDP_PROVIDERS_QUERY_KEY = ["public", "idp-providers"];

/** Public, unauthenticated list of configured login IDPs -- backs the
 * "Let's Get Started" login screen's provider buttons when
 * ``ENABLE_OAUTH_V2_LOGIN`` is on. Unlike ``useAuthProviders``, this never
 * requires a session and is safe to call from the unauthenticated login
 * route. */
export const usePublicIdpProviders = (enabled: boolean) =>
  useQuery({
    queryKey: PUBLIC_IDP_PROVIDERS_QUERY_KEY,
    queryFn: () => authProvidersService.listPublicIdpProviders(),
    enabled,
  });
