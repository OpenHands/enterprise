import { http, HttpResponse } from "msw";
import type { ServiceAccount } from "#/api/organization-service/service-accounts.api";
import { CustomSecretWithoutValue } from "#/api/secrets-service.types";

interface MockServiceAccountState {
  accounts: ServiceAccount[];
  /** Secret names and descriptions per service account id. Values are never stored. */
  secrets: Map<string, CustomSecretWithoutValue[]>;
  /** Per-user monthly budget overrides, by service account id. */
  budgets: Map<string, number>;
}

const createState = (): MockServiceAccountState => ({
  accounts: [],
  secrets: new Map(),
  budgets: new Map(),
});

let nextId = 1;
const stateByOrgId = new Map<string, MockServiceAccountState>();

/** Each org starts with one service account, created on first use. */
const getState = (orgId: string): MockServiceAccountState => {
  let state = stateByOrgId.get(orgId);
  if (!state) {
    state = createState();
    state.accounts.push({
      id: `sa-${nextId}`,
      org_id: orgId,
      name: "nightly-report",
      is_disabled: false,
      role: "service_account",
    });
    nextId += 1;
    stateByOrgId.set(orgId, state);
  }
  return state;
};

export const resetServiceAccountMockData = () => {
  stateByOrgId.clear();
  nextId = 1;
};

const notFound = () =>
  HttpResponse.json({ detail: "Service account not found" }, { status: 404 });

const findAccount = (orgId: string, saId: string) =>
  getState(orgId).accounts.find((account) => account.id === saId);

/**
 * Budget rows of the org's service accounts, for the mock budget list. The
 * backend returns them with `account_type: "service"` and matches the user
 * search against the display name.
 */
export const getServiceAccountBudgetRows = (orgId: string, search?: string) =>
  getState(orgId)
    .accounts.filter(
      (account) =>
        !search || account.name.toLowerCase().includes(search.toLowerCase()),
    )
    .map((account) => {
      const limit = getState(orgId).budgets.get(account.id) ?? null;
      return {
        user_id: account.id,
        user_email: null,
        user_name: account.name,
        account_type: "service" as const,
        current_spend: 12.5,
        monthly_limit: limit,
        effective_monthly_limit: limit ?? 250,
        is_disabled: false,
        is_override: limit !== null,
      };
    });

/** Applies a budget override to a service account. Returns its row, or undefined for any other user. */
export const setServiceAccountBudget = (
  orgId: string,
  userId: string,
  monthlyLimit: number | null,
) => {
  const state = getState(orgId);
  if (!state.accounts.some((account) => account.id === userId)) {
    return undefined;
  }
  if (monthlyLimit === null) state.budgets.delete(userId);
  else state.budgets.set(userId, monthlyLimit);
  return getServiceAccountBudgetRows(orgId).find(
    (row) => row.user_id === userId,
  );
};

const BASE = "/api/organizations/:orgId/service-accounts";

export const SERVICE_ACCOUNT_HANDLERS = [
  http.get(BASE, ({ params }) =>
    HttpResponse.json({
      items: getState(String(params.orgId)).accounts,
      next_page_id: null,
    }),
  ),

  http.post(BASE, async ({ params, request }) => {
    const orgId = String(params.orgId);
    const { name } = (await request.json()) as { name: string };
    const state = getState(orgId);
    if (state.accounts.some((account) => account.name === name)) {
      return HttpResponse.json(
        { detail: "A service account with this name already exists" },
        { status: 409 },
      );
    }
    const account: ServiceAccount = {
      id: `sa-${nextId}`,
      org_id: orgId,
      name,
      is_disabled: false,
      role: "service_account",
    };
    nextId += 1;
    state.accounts.push(account);
    return HttpResponse.json(account, { status: 201 });
  }),

  http.patch(`${BASE}/:saId`, async ({ params, request }) => {
    const account = findAccount(String(params.orgId), String(params.saId));
    if (!account) return notFound();
    const { name } = (await request.json()) as { name: string };
    account.name = name;
    return HttpResponse.json(account);
  }),

  http.post(`${BASE}/:saId/disable`, ({ params }) => {
    const account = findAccount(String(params.orgId), String(params.saId));
    if (!account) return notFound();
    account.is_disabled = true;
    return HttpResponse.json(account);
  }),

  http.post(`${BASE}/:saId/enable`, ({ params }) => {
    const account = findAccount(String(params.orgId), String(params.saId));
    if (!account) return notFound();
    account.is_disabled = false;
    return HttpResponse.json(account);
  }),

  http.delete(`${BASE}/:saId`, ({ params }) => {
    const state = getState(String(params.orgId));
    const index = state.accounts.findIndex(
      (account) => account.id === params.saId,
    );
    if (index < 0) return notFound();
    state.accounts.splice(index, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get(`${BASE}/:saId/secrets`, ({ params }) => {
    const state = getState(String(params.orgId));
    return HttpResponse.json({
      items: state.secrets.get(String(params.saId)) ?? [],
      next_page_id: null,
    });
  }),

  http.put(`${BASE}/:saId/secrets/:secretName`, async ({ params, request }) => {
    const state = getState(String(params.orgId));
    const saId = String(params.saId);
    const name = String(params.secretName);
    const body = (await request.json()) as { description?: string };
    const secrets = state.secrets.get(saId) ?? [];
    const existing = secrets.find((secret) => secret.name === name);
    // Like the backend: omitting the description keeps the old one.
    const description = body.description ?? existing?.description;
    state.secrets.set(saId, [
      ...secrets.filter((secret) => secret.name !== name),
      { name, description, scope: "personal" },
    ]);
    return HttpResponse.json({ message: "Secret saved successfully" });
  }),

  http.delete(`${BASE}/:saId/secrets/:secretName`, ({ params }) => {
    const state = getState(String(params.orgId));
    const saId = String(params.saId);
    const secrets = state.secrets.get(saId) ?? [];
    if (!secrets.some((secret) => secret.name === params.secretName)) {
      return HttpResponse.json({ detail: "Secret not found" }, { status: 404 });
    }
    state.secrets.set(
      saId,
      secrets.filter((secret) => secret.name !== params.secretName),
    );
    return HttpResponse.json({ message: "Secret deleted successfully" });
  }),
];
