import { http, HttpResponse } from "msw";
import type {
  ScimCredential,
  ScimProviderOption,
} from "#/api/organization-service/scim.api";

const OKTA = { id: 7, title: "Okta", protocol: "oidc" };
const ENTRA = { id: 8, title: "Microsoft Entra ID", protocol: "saml" };
const PROVIDERS = [OKTA, ENTRA];

let nextId = 1;
let canBindProvider = import.meta.env.VITE_MOCK_SCIM_ORG_ADMIN !== "true";
const credentialsByOrgId = new Map<string, ScimCredential[]>();

/** Each org starts with one active Okta credential, created on first use. */
const getCredentials = (orgId: string): ScimCredential[] => {
  let credentials = credentialsByOrgId.get(orgId);
  if (!credentials) {
    credentials = [
      {
        id: `scim-${nextId}`,
        name: "Okta production",
        auth_provider_id: OKTA.id,
        auth_provider_title: OKTA.title,
        created_at: "2026-10-01T09:00:00Z",
        last_used_at: "2026-11-20T09:00:00Z",
        revoked_at: null,
      },
    ];
    nextId += 1;
    credentialsByOrgId.set(orgId, credentials);
  }
  return credentials;
};

/** Sets an org's credentials outright, e.g. `[]` for an unbound org. */
export const setScimMockCredentials = (
  orgId: string,
  credentials: ScimCredential[],
) => {
  credentialsByOrgId.set(orgId, credentials);
};

/** Whether the mock user may bind a provider, as an instance Super Admin may. */
export const setScimMockCanBindProvider = (value: boolean) => {
  canBindProvider = value;
};

export const resetScimMockData = () => {
  credentialsByOrgId.clear();
  nextId = 1;
  canBindProvider = import.meta.env.VITE_MOCK_SCIM_ORG_ADMIN !== "true";
};

const boundProviderIds = (orgId: string) =>
  new Set(
    getCredentials(orgId)
      .filter((credential) => credential.revoked_at === null)
      .map((credential) => credential.auth_provider_id),
  );

/** Like the API: a Super Admin is offered every provider, an org admin the bound ones. */
const providerOptions = (orgId: string): ScimProviderOption[] => {
  const bound = boundProviderIds(orgId);
  return PROVIDERS.filter(
    (provider) => canBindProvider || bound.has(provider.id),
  ).map((provider) => ({
    ...provider,
    bound: bound.has(provider.id),
    allow_match_by_email: true,
  }));
};

export const SCIM_HANDLERS = [
  http.get("/api/organizations/:orgId/scim", ({ params }) =>
    HttpResponse.json({
      base_url: `${window.location.origin}/scim/v2`,
      can_bind_provider: canBindProvider,
      providers: providerOptions(params.orgId as string),
    }),
  ),

  http.get("/api/organizations/:orgId/scim/credentials", ({ params }) => {
    const credentials = getCredentials(params.orgId as string);
    return HttpResponse.json({
      items: [
        ...credentials.filter((credential) => credential.revoked_at === null),
        ...credentials.filter((credential) => credential.revoked_at !== null),
      ],
    });
  }),

  http.post(
    "/api/organizations/:orgId/scim/credentials",
    async ({ params, request }) => {
      const orgId = params.orgId as string;
      const body = (await request.json()) as {
        name?: string;
        auth_provider_id?: number;
      };
      const provider = PROVIDERS.find(
        (option) => option.id === body.auth_provider_id,
      );
      if (
        !canBindProvider &&
        !boundProviderIds(orgId).has(provider?.id ?? -1)
      ) {
        return HttpResponse.json(
          {
            detail:
              "Only a Super Admin can connect this organization to an SSO provider it has no active SCIM credential for",
          },
          { status: 403 },
        );
      }
      if (!provider || !body.name?.trim()) {
        return HttpResponse.json(
          { detail: "Invalid request" },
          { status: 400 },
        );
      }
      const credential: ScimCredential = {
        id: `scim-${nextId}`,
        name: body.name.trim(),
        auth_provider_id: provider.id,
        auth_provider_title: provider.title,
        created_at: new Date().toISOString(),
        last_used_at: null,
        revoked_at: null,
      };
      getCredentials(orgId).unshift(credential);
      const token = `ohscim_mock-token-${nextId}`;
      nextId += 1;
      return HttpResponse.json({ ...credential, token }, { status: 201 });
    },
  ),

  http.delete(
    "/api/organizations/:orgId/scim/credentials/:credentialId",
    ({ params }) => {
      const credential = getCredentials(params.orgId as string).find(
        (item) => item.id === params.credentialId,
      );
      if (!credential) {
        return HttpResponse.json(
          { detail: "SCIM credential not found" },
          { status: 404 },
        );
      }
      credential.revoked_at ??= new Date().toISOString();
      return new HttpResponse(null, { status: 204 });
    },
  ),
];
