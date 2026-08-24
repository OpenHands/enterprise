import { useQuery } from "@tanstack/react-query";
import { quotaService } from "#/api/quota-service/quota-service.api";

export const QUOTA_QUERY_KEYS = {
  status: ["quota", "status"] as const,
};

export const useQuotaStatus = () =>
  useQuery({
    queryKey: QUOTA_QUERY_KEYS.status,
    queryFn: quotaService.getStatus,
    refetchInterval: 60_000, // refresh every minute so the countdown stays live
  });
