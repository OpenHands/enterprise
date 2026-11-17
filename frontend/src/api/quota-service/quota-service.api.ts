import { openHands } from "#/api/open-hands-axios";

export interface QuotaStatus {
  daily_limit: number | null;
  used_today: number;
  remaining: number | null;
  reset_at: string;
  work_email: string | null;
  work_email_verified: boolean;
  latest_request_status: string | null;
  latest_request_requested_limit: number | null;
}

export interface UserQuota {
  user_id: string;
  daily_sandbox_limit: number | null;
}

export const quotaService = {
  getUserQuota: async (userId: string): Promise<UserQuota> => {
    const { data } = await openHands.get<UserQuota>(
      `/api/admin/quota/users/${userId}/quota`,
    );
    return data;
  },
  updateUserQuota: async ({
    userId,
    dailySandboxLimit,
  }: {
    userId: string;
    dailySandboxLimit: number | null;
  }): Promise<UserQuota> => {
    const { data } = await openHands.put<UserQuota>(
      `/api/admin/quota/users/${userId}/quota`,
      { daily_sandbox_limit: dailySandboxLimit },
    );
    return data;
  },

  getStatus: async (): Promise<QuotaStatus> => {
    const { data } = await openHands.get<QuotaStatus>("/api/quota/status");
    return data;
  },
};
