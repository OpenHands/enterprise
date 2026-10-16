import { http, HttpResponse } from "msw";
import type { QuotaStatus } from "#/api/quota-service/quota-service.api";

export const QUOTA_HANDLERS = [
  http.get("/api/quota/status", () =>
    HttpResponse.json<QuotaStatus>({
      daily_limit: null,
      used_today: 0,
      remaining: null,
      reset_at: new Date(Date.now() + 86_400_000).toISOString(),
      work_email: null,
      work_email_verified: false,
      latest_request_status: null,
      latest_request_requested_limit: null,
    }),
  ),
];
