import { openHands } from "../open-hands-axios";

/**
 * A shared provider connection: an `api_key` + optional `base_url` that one or
 * more LLM profiles reference by id. The credential is resolved into a
 * profile's runnable LLM at activation time; this service only manages the
 * stored connections.
 *
 * Org-scoped CRUD lives at `/api/organizations/{orgId}/provider-connections`
 * (see `server/routes/org_provider_connections.py`). Listing requires
 * `VIEW_ORG_SETTINGS`; create/update/delete require `EDIT_ORG_SETTINGS`.
 * Neither route exposes the stored key — each item carries `api_key_set`.
 */
export interface ProviderConnection {
  id: string;
  display_name: string;
  provider: string;
  base_url: string | null;
  created_at: number;
  updated_at: number;
  /** Whether the stored connection currently holds a usable key. */
  api_key_set: boolean;
}

export interface CreateProviderConnectionRequest {
  display_name: string;
  provider: string;
  api_key: string;
  base_url?: string | null;
}

/**
 * Partial update. Only `base_url` may be set to null (to clear it). The
 * backend rejects `api_key: null` — a connection must always keep a key — so
 * callers omit it to leave the key unchanged.
 */
export interface UpdateProviderConnectionRequest {
  display_name?: string;
  provider?: string;
  api_key?: string;
  base_url?: string | null;
}

interface ProviderConnectionListResponse {
  connections: ProviderConnection[];
}

class OrgProviderConnectionsService {
  static async list(orgId: string): Promise<ProviderConnectionListResponse> {
    const { data } = await openHands.get<ProviderConnectionListResponse>(
      `/api/organizations/${orgId}/provider-connections`,
    );
    return data;
  }

  static async create(
    orgId: string,
    request: CreateProviderConnectionRequest,
  ): Promise<ProviderConnection> {
    const { data } = await openHands.post<ProviderConnection>(
      `/api/organizations/${orgId}/provider-connections`,
      request,
    );
    return data;
  }

  static async update(
    orgId: string,
    id: string,
    request: UpdateProviderConnectionRequest,
  ): Promise<ProviderConnection> {
    const { data } = await openHands.patch<ProviderConnection>(
      `/api/organizations/${orgId}/provider-connections/${encodeURIComponent(id)}`,
      request,
    );
    return data;
  }

  static async delete(orgId: string, id: string): Promise<ProviderConnection> {
    const { data } = await openHands.delete<ProviderConnection>(
      `/api/organizations/${orgId}/provider-connections/${encodeURIComponent(id)}`,
    );
    return data;
  }
}

export default OrgProviderConnectionsService;
