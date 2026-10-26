import { openHands } from "../open-hands-axios";
import { CustomSecretPage } from "#/api/secrets-service.types";

/** A service account of an organization (`ServiceAccountResponse`). */
export interface ServiceAccount {
  id: string;
  org_id: string;
  name: string;
  is_disabled: boolean;
  role: string;
}

interface ServiceAccountPage {
  items: ServiceAccount[];
  next_page_id: string | null;
}

const PAGE_SIZE = 100;

const basePath = (orgId: string) =>
  `/api/organizations/${orgId}/service-accounts`;

/**
 * Admin API for the service accounts of an organization. Needs the
 * `manage_service_accounts` permission. Secret values are write-only: no
 * route returns them.
 */
export const serviceAccountsService = {
  /** Lists every service account of the org, following the page cursor. */
  list: async (orgId: string): Promise<ServiceAccount[]> => {
    const items: ServiceAccount[] = [];
    let pageId: string | null = null;
    do {
      // eslint-disable-next-line no-await-in-loop
      const { data }: { data: ServiceAccountPage } = await openHands.get(
        basePath(orgId),
        {
          params: { limit: PAGE_SIZE, ...(pageId ? { page_id: pageId } : {}) },
        },
      );
      items.push(...data.items);
      pageId = data.next_page_id;
    } while (pageId);
    return items;
  },

  create: async (orgId: string, name: string) => {
    const { data } = await openHands.post<ServiceAccount>(basePath(orgId), {
      name,
    });
    return data;
  },

  rename: async (orgId: string, saId: string, name: string) => {
    const { data } = await openHands.patch<ServiceAccount>(
      `${basePath(orgId)}/${saId}`,
      { name },
    );
    return data;
  },

  disable: async (orgId: string, saId: string) => {
    const { data } = await openHands.post<ServiceAccount>(
      `${basePath(orgId)}/${saId}/disable`,
    );
    return data;
  },

  enable: async (orgId: string, saId: string) => {
    const { data } = await openHands.post<ServiceAccount>(
      `${basePath(orgId)}/${saId}/enable`,
    );
    return data;
  },

  delete: async (orgId: string, saId: string) => {
    await openHands.delete(`${basePath(orgId)}/${saId}`);
  },

  /** Names and descriptions of the service account's secrets. */
  listSecrets: async (orgId: string, saId: string) => {
    const { data } = await openHands.get<CustomSecretPage>(
      `${basePath(orgId)}/${saId}/secrets`,
    );
    return data;
  },

  /** Creates or replaces one secret. Omitting `description` keeps the old one. */
  putSecret: async (
    orgId: string,
    saId: string,
    name: string,
    body: { value: string; description?: string },
  ) => {
    await openHands.put(
      `${basePath(orgId)}/${saId}/secrets/${encodeURIComponent(name)}`,
      body,
    );
  },

  deleteSecret: async (orgId: string, saId: string, name: string) => {
    await openHands.delete(
      `${basePath(orgId)}/${saId}/secrets/${encodeURIComponent(name)}`,
    );
  },
};
