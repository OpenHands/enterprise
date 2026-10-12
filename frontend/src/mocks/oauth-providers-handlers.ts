import { http, HttpResponse } from "msw";
import type {
  CreateOAuthProviderParams,
  OAuthProvider,
  UpdateOAuthProviderParams,
} from "#/api/oauth-providers-service/oauth-providers.types";

const MOCK_OAUTH_PROVIDERS: OAuthProvider[] = [
  {
    id: 1,
    provider_category: "github",
    display_name: "GitHub",
    is_idp: false,
    client_id: "mock-github-client-id",
    has_client_secret: true,
    authorization_url: null,
    token_url: null,
    userinfo_url: null,
    scopes: ["repo", "user"],
    permitted_drift_seconds: 60,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
  {
    id: 2,
    provider_category: "enterprise_sso",
    display_name: "Acme SSO",
    is_idp: true,
    client_id: "mock-sso-client-id",
    has_client_secret: false,
    authorization_url: "https://sso.acme.example/authorize",
    token_url: "https://sso.acme.example/token",
    userinfo_url: "https://sso.acme.example/userinfo",
    scopes: ["openid", "email"],
    permitted_drift_seconds: 30,
    created_at: "2024-01-02T00:00:00Z",
    updated_at: "2024-01-02T00:00:00Z",
  },
];

let oauthProviders = [...MOCK_OAUTH_PROVIDERS];
let nextId = oauthProviders.length + 1;

export const resetOAuthProvidersMockState = () => {
  oauthProviders = [...MOCK_OAUTH_PROVIDERS];
  nextId = oauthProviders.length + 1;
};

export const OAUTH_PROVIDERS_HANDLERS = [
  http.get("/api/admin/oauth-providers", () =>
    HttpResponse.json(oauthProviders),
  ),

  http.post("/api/admin/oauth-providers", async ({ request }) => {
    const body = (await request.json()) as CreateOAuthProviderParams;
    const id = nextId;
    nextId += 1;
    const created: OAuthProvider = {
      id,
      provider_category: body.provider_category,
      display_name: body.display_name,
      is_idp: body.is_idp ?? false,
      client_id: body.client_id,
      has_client_secret: !!body.client_secret,
      authorization_url: body.authorization_url ?? null,
      token_url: body.token_url ?? null,
      userinfo_url: body.userinfo_url ?? null,
      scopes: body.scopes ?? null,
      permitted_drift_seconds: body.permitted_drift_seconds ?? 60,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    oauthProviders = [created, ...oauthProviders];
    return HttpResponse.json(created, { status: 201 });
  }),

  http.patch("/api/admin/oauth-providers/:id", async ({ params, request }) => {
    const id = Number(params.id);
    const existing = oauthProviders.find((provider) => provider.id === id);
    if (!existing) {
      return HttpResponse.json(
        { detail: "OAuth provider not found" },
        { status: 404 },
      );
    }
    const body = (await request.json()) as UpdateOAuthProviderParams;
    const updated: OAuthProvider = {
      ...existing,
      ...(body.provider_category !== undefined && {
        provider_category: body.provider_category,
      }),
      ...(body.display_name !== undefined && {
        display_name: body.display_name,
      }),
      ...(body.is_idp !== undefined && { is_idp: body.is_idp }),
      ...(body.client_id !== undefined && { client_id: body.client_id }),
      ...(body.client_secret !== undefined && {
        has_client_secret: body.client_secret !== "",
      }),
      ...(body.authorization_url !== undefined && {
        authorization_url: body.authorization_url,
      }),
      ...(body.token_url !== undefined && { token_url: body.token_url }),
      ...(body.userinfo_url !== undefined && {
        userinfo_url: body.userinfo_url,
      }),
      ...(body.scopes !== undefined && { scopes: body.scopes }),
      ...(body.permitted_drift_seconds !== undefined && {
        permitted_drift_seconds: body.permitted_drift_seconds,
      }),
      updated_at: new Date().toISOString(),
    };
    oauthProviders = oauthProviders.map((provider) =>
      provider.id === id ? updated : provider,
    );
    return HttpResponse.json(updated);
  }),

  http.delete("/api/admin/oauth-providers/:id", ({ params }) => {
    const id = Number(params.id);
    const existing = oauthProviders.find((provider) => provider.id === id);
    if (!existing) {
      return HttpResponse.json(
        { detail: "OAuth provider not found" },
        { status: 404 },
      );
    }
    oauthProviders = oauthProviders.filter((provider) => provider.id !== id);
    return HttpResponse.json(null, { status: 204 });
  }),
];
