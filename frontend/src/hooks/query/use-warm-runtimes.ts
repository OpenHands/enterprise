import { useQuery } from "@tanstack/react-query";
import { warmRuntimesService } from "#/api/warm-runtimes-service/warm-runtimes-service.api";

export const WARM_RUNTIMES_QUERY_KEY = ["super-admin", "warm-runtimes"];

export const warmRuntimeAccessQueryKey = (name: string) => [
  ...WARM_RUNTIMES_QUERY_KEY,
  name,
  "access",
];

export const useWarmRuntimeAccess = (name: string) =>
  useQuery({
    queryKey: warmRuntimeAccessQueryKey(name),
    queryFn: () => warmRuntimesService.getWarmRuntimeAccess(name),
  });

export const useWarmRuntimes = () =>
  useQuery({
    queryKey: WARM_RUNTIMES_QUERY_KEY,
    queryFn: () => warmRuntimesService.listWarmRuntimes(),
  });
