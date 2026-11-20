import { useQuery } from "@tanstack/react-query";
import { quotaService } from "#/api/quota-service/quota-service.api";

export const QUOTA_QUERY_KEYS = {
  user: (userId: string) => ["quota", "user", userId] as const,
  status: ["quota", "status"] as const,
};

export const useQuotaStatus = (options?: { enabled?: boolean }) =>
  useQuery({
    queryKey: QUOTA_QUERY_KEYS.status,
    queryFn: quotaService.getStatus,
    refetchInterval: 60_000, // refresh every minute so the countdown stays live
    enabled: options?.enabled,
  });

export const useUserQuota = (userId: string) =>
  useQuery({
    queryKey: QUOTA_QUERY_KEYS.user(userId),
    queryFn: () => quotaService.getUserQuota(userId),
    staleTime: 0,
    meta: { disableToast: true },
  });
