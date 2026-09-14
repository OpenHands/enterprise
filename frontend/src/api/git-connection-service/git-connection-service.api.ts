import { openHands } from "../open-hands-axios";

export const NATIVE_GIT_PROVIDERS = ["github", "gitlab", "bitbucket"] as const;
export type NativeGitProvider = (typeof NATIVE_GIT_PROVIDERS)[number];
export type GitAuthMethod = "pat" | "api_token" | "oauth";

export interface GitConnection {
  provider: NativeGitProvider;
  host: string;
  auth_type: GitAuthMethod;
  status: "connected" | "reconnect_required";
  account: {
    id: string | null;
    login: string | null;
    display_name: string | null;
    avatar_url: string | null;
  };
  last_error: string | null;
}
export interface GitCapability {
  installation_available?: boolean;
  webhook_host?: string;
  methods: GitAuthMethod[];
  hosts: string[];
  default_host: string;
}
export interface GitConnectionsResponse {
  connections: GitConnection[];
  capabilities: Partial<Record<NativeGitProvider, GitCapability>>;
}
export const GitConnectionService = {
  list: async (): Promise<GitConnectionsResponse> =>
    (await openHands.get<GitConnectionsResponse>("/api/git-connections")).data,
  save: async ({
    provider,
    ...input
  }: {
    provider: NativeGitProvider;
    host: string;
    token: string;
    email?: string;
  }): Promise<void> => {
    await openHands.put(`/api/git-connections/${provider}`, input);
  },
  authorize: async ({
    provider,
    host,
  }: {
    provider: NativeGitProvider;
    host: string;
  }): Promise<{ authorization_url: string }> =>
    (
      await openHands.post<{ authorization_url: string }>(
        `/api/git-connections/${provider}/oauth`,
        { host },
      )
    ).data,
  disconnect: async ({
    provider,
    host,
  }: {
    provider: NativeGitProvider;
    host: string;
  }): Promise<void> => {
    await openHands.delete(`/api/git-connections/${provider}`, {
      params: { host },
    });
  },
  installation: async (): Promise<{ installation_url: string }> =>
    (
      await openHands.get<{ installation_url: string }>(
        "/api/git-connections/github/installation",
      )
    ).data,
};
