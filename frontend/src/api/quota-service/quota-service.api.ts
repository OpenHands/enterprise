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

export interface CreateQuotaIncreaseRequest {
  work_email: string;
  requested_limit: number;
  reason?: string;
}

export interface QuotaIncreaseRequestResponse {
  id: number;
  status: string;
  work_email: string;
  baseline_limit: number;
  requested_limit: number;
  reason: string | null;
  created_at: string;
}

export const quotaService = {
  getStatus: async (): Promise<QuotaStatus> => {
    const { data } = await openHands.get<QuotaStatus>("/api/quota/status");
    return data;
  },

  createIncreaseRequest: async (
    body: CreateQuotaIncreaseRequest,
  ): Promise<QuotaIncreaseRequestResponse> => {
    const { data } = await openHands.post<QuotaIncreaseRequestResponse>(
      "/api/quota/increase-request",
      body,
    );
    return data;
  },
};
