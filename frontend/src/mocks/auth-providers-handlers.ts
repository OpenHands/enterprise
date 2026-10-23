import { http, HttpResponse } from "msw";
import type {
  AuthProvider,
  AuthProviderUserLink,
  CreateAuthProviderParams,
  UpdateAuthProviderParams,
} from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { findMockAdminUser } from "./super-admin-handlers";

const MOCK_AUTH_PROVIDERS: AuthProvider[] = [
  {
    id: 1,
    kind: "OAuthProviderDetails",
    title: "GitHub",
    is_idp: false,
    icon: null,
    color: DEFAULT_AUTH_PROVIDER_COLOR,
    enabled: true,
    allow_registration: true,
    allow_match_by_email: true,
    details: {
      kind: "OAuthProviderDetails",
      provider_type: "github",
      client_id: "mock-github-client-id",
      client_secret: "**********",
      authorization_url: null,
      token_url: null,
      userinfo_url: null,
      scopes: ["repo", "user"],
      permitted_drift_seconds: 60,
      legacy_keycloak_type: null,
    },
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
  {
    id: 2,
    kind: "OAuthProviderDetails",
    title: "Acme SSO",
    is_idp: true,
    icon: null,
    color: "#2563eb",
    enabled: true,
    allow_registration: true,
    allow_match_by_email: true,
    details: {
      kind: "OAuthProviderDetails",
      provider_type: "enterprise_sso",
      client_id: "mock-sso-client-id",
      client_secret: null,
      authorization_url: "https://sso.acme.example/authorize",
      token_url: "https://sso.acme.example/token",
      userinfo_url: "https://sso.acme.example/userinfo",
      scopes: ["openid", "email"],
      permitted_drift_seconds: 30,
      legacy_keycloak_type: null,
    },
    created_at: "2024-01-02T00:00:00Z",
    updated_at: "2024-01-02T00:00:00Z",
  },
];

let authProviders = [...MOCK_AUTH_PROVIDERS];
let nextId = authProviders.length + 1;

/** ``auth_provider_users`` rows, keyed by ``oauth_provider_id`` -- mirrors
 * ``storage.auth_provider_user.AuthProviderUser``. ``email``/``user_name``
 * are resolved from ``MOCK_ADMIN_USERS`` (via ``findMockAdminUser``) at
 * response time, the same way the real endpoint joins against ``User``, so
 * re-pointing a link picks up the new user's email immediately. */
type MockAuthProviderUserLink = Omit<
  AuthProviderUserLink,
  "email" | "user_name"
>;

const MOCK_AUTH_PROVIDER_USER_LINKS: Record<
  number,
  MockAuthProviderUserLink[]
> = {
  1: [
    {
      id: 101,
      user_id: "99",
      external_subject_id: "gh-109284",
      external_email: "me-old@acme.org",
    },
    {
      id: 102,
      user_id: "u-2",
      external_subject_id: "gh-337510",
      external_email: null,
    },
  ],
  2: [],
};

let authProviderUserLinks = structuredClone(MOCK_AUTH_PROVIDER_USER_LINKS);

export const resetAuthProvidersMockState = () => {
  authProviders = [...MOCK_AUTH_PROVIDERS];
  nextId = authProviders.length + 1;
  authProviderUserLinks = structuredClone(MOCK_AUTH_PROVIDER_USER_LINKS);
};

const toAuthProviderUserLink = (
  link: MockAuthProviderUserLink,
): AuthProviderUserLink => {
  const user = findMockAdminUser(link.user_id);
  return {
    ...link,
    email: user?.email ?? null,
    user_name: user?.name ?? null,
  };
};

export const AUTH_PROVIDERS_HANDLERS = [
  http.get("/api/admin/oauth-providers", () =>
    HttpResponse.json(authProviders),
  ),

  http.get("/api/admin/oauth-providers/:id", ({ params }) => {
    const id = Number(params.id);
    const existing = authProviders.find((provider) => provider.id === id);
    if (!existing) {
      return HttpResponse.json(
        { detail: "OAuth provider not found" },
        { status: 404 },
      );
    }
    return HttpResponse.json(existing);
  }),

  http.post("/api/admin/oauth-providers", async ({ request }) => {
    const body = (await request.json()) as CreateAuthProviderParams;
    const id = nextId;
    nextId += 1;
    const created: AuthProvider = {
      id,
      kind: body.details.kind,
      title: body.title,
      is_idp: body.is_idp ?? false,
      icon: body.icon ?? null,
      color: body.color ?? DEFAULT_AUTH_PROVIDER_COLOR,
      enabled: body.enabled ?? false,
      allow_registration: body.allow_registration ?? true,
      allow_match_by_email: body.allow_match_by_email ?? true,
      details: body.details,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    authProviders = [created, ...authProviders];
    return HttpResponse.json(created, { status: 201 });
  }),

  http.patch("/api/admin/oauth-providers/:id", async ({ params, request }) => {
    const id = Number(params.id);
    const existing = authProviders.find((provider) => provider.id === id);
    if (!existing) {
      return HttpResponse.json(
        { detail: "OAuth provider not found" },
        { status: 404 },
      );
    }
    const body = (await request.json()) as UpdateAuthProviderParams;
    const updated: AuthProvider = {
      ...existing,
      ...(body.title !== undefined && { title: body.title }),
      ...(body.is_idp !== undefined && { is_idp: body.is_idp }),
      ...(body.icon !== undefined && { icon: body.icon }),
      ...(body.color !== undefined && { color: body.color }),
      ...(body.enabled !== undefined && { enabled: body.enabled }),
      ...(body.allow_registration !== undefined && {
        allow_registration: body.allow_registration,
      }),
      ...(body.allow_match_by_email !== undefined && {
        allow_match_by_email: body.allow_match_by_email,
      }),
      ...(body.details !== undefined && {
        details: body.details,
        kind: body.details.kind,
      }),
      updated_at: new Date().toISOString(),
    };
    authProviders = authProviders.map((provider) =>
      provider.id === id ? updated : provider,
    );
    return HttpResponse.json(updated);
  }),

  http.delete("/api/admin/oauth-providers/:id", ({ params }) => {
    const id = Number(params.id);
    const existing = authProviders.find((provider) => provider.id === id);
    if (!existing) {
      return HttpResponse.json(
        { detail: "OAuth provider not found" },
        { status: 404 },
      );
    }
    authProviders = authProviders.filter((provider) => provider.id !== id);
    return HttpResponse.json(null, { status: 204 });
  }),

  // Mirrors ``server.routes.oauth_providers_admin.list_auth_provider_users``:
  // offset pagination via ``page_id`` and a case-insensitive ``q`` filter
  // over the linked user's email, external email, and external subject id.
  http.get("/api/admin/oauth-providers/:id/users", ({ params, request }) => {
    const id = Number(params.id);
    if (!authProviders.some((provider) => provider.id === id)) {
      return HttpResponse.json(
        { detail: "OAuth provider not found" },
        { status: 404 },
      );
    }

    const url = new URL(request.url);
    const offset = Number(url.searchParams.get("page_id") ?? "0");
    const limit = Number(url.searchParams.get("limit") ?? "20");
    const q = url.searchParams.get("q")?.trim().toLowerCase();

    const allLinks = (authProviderUserLinks[id] ?? []).map(
      toAuthProviderUserLink,
    );
    const filtered = q
      ? allLinks.filter((link) =>
          [
            link.email,
            link.user_name,
            link.external_email,
            link.external_subject_id,
          ].some((value) => value?.toLowerCase().includes(q)),
        )
      : allLinks;

    return HttpResponse.json({
      items: filtered.slice(offset, offset + limit),
      total: filtered.length,
      current_page: Math.floor(offset / limit) + 1,
      per_page: limit,
    });
  }),

  // Mirrors ``server.routes.oauth_providers_admin.update_auth_provider_user``.
  http.patch(
    "/api/admin/oauth-providers/:id/users/:linkId",
    async ({ params, request }) => {
      const id = Number(params.id);
      const linkId = Number(params.linkId);
      if (!authProviders.some((provider) => provider.id === id)) {
        return HttpResponse.json(
          { detail: "OAuth provider not found" },
          { status: 404 },
        );
      }

      const links = authProviderUserLinks[id] ?? [];
      const link = links.find((candidate) => candidate.id === linkId);
      if (!link) {
        return HttpResponse.json(
          { detail: "User link not found" },
          { status: 404 },
        );
      }

      const body = (await request.json()) as { user_id: string };
      const targetUser = findMockAdminUser(body.user_id);
      if (!targetUser) {
        return HttpResponse.json(
          { detail: "Target user not found" },
          { status: 404 },
        );
      }
      const conflict = links.some(
        (candidate) =>
          candidate.id !== linkId && candidate.user_id === body.user_id,
      );
      if (conflict) {
        return HttpResponse.json(
          { detail: "The target user already has a link on this provider" },
          { status: 409 },
        );
      }

      link.user_id = body.user_id;
      return HttpResponse.json(toAuthProviderUserLink(link));
    },
  ),
];
