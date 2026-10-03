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

export const quotaService = {
  getStatus: async (): Promise<QuotaStatus> => {
    const { data } = await openHands.get<QuotaStatus>("/api/quota/status");
    return data;
  },
};
