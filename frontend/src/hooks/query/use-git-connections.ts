import type { UseQueryResult, DefaultError } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import type { GitConnectionsResponse } from "#/api/git-connection-service/git-connection-service.api";
import { GitConnectionService } from "#/api/git-connection-service/git-connection-service.api";
import { useConfig } from "./use-config";
import { useIsAuthed } from "./use-is-authed";

export function useGitConnections(): UseQueryResult<
  GitConnectionsResponse,
  DefaultError
> {
  const { data: config } = useConfig();
  const { data: isAuthed } = useIsAuthed();
  return useQuery({
    queryKey: ["git-connections"],
    queryFn: GitConnectionService.list,
    enabled: config?.auth_mode === "native" && isAuthed === true,
    retry: false,
    meta: { skipAuthInvalidation: true, disableToast: true },
  });
}
