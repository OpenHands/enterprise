import { http, HttpResponse } from "msw";
import type {
  AuthProvider,
  CreateAuthProviderParams,
  UpdateAuthProviderParams,
} from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";

const MOCK_AUTH_PROVIDERS: AuthProvider[] = [
  {
    id: 1,
    kind: "OAuthProviderDetails",
    title: "GitHub",
    is_idp: false,
    icon: null,
    color: DEFAULT_AUTH_PROVIDER_COLOR,
    allow_registration: true,
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
    allow_registration: true,
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
    },
    created_at: "2024-01-02T00:00:00Z",
    updated_at: "2024-01-02T00:00:00Z",
  },
];

let authProviders = [...MOCK_AUTH_PROVIDERS];
let nextId = authProviders.length + 1;

export const resetAuthProvidersMockState = () => {
  authProviders = [...MOCK_AUTH_PROVIDERS];
  nextId = authProviders.length + 1;
};

export const AUTH_PROVIDERS_HANDLERS = [
  http.get("/api/admin/oauth-providers", () =>
    HttpResponse.json(authProviders),
  ),

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
      allow_registration: body.allow_registration ?? true,
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
      ...(body.allow_registration !== undefined && {
        allow_registration: body.allow_registration,
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
];
