import { useQueryClient } from "@tanstack/react-query";
import { GitConnectionService } from "#/api/git-connection-service/git-connection-service.api";
import {
  useEphemeralMutation,
  EphemeralMutationFor,
} from "./use-ephemeral-mutation";
import { SETTINGS_QUERY_KEYS } from "../query/query-keys";

export const useSaveGitCredential = (): EphemeralMutationFor<
  typeof GitConnectionService.save
> => useEphemeralMutation(GitConnectionService.save);
export const useConnectGitProvider = (): EphemeralMutationFor<
  typeof GitConnectionService.authorize
> => useEphemeralMutation(GitConnectionService.authorize);
export const useDisconnectGitConnection = (): EphemeralMutationFor<
  typeof GitConnectionService.disconnect
> => useEphemeralMutation(GitConnectionService.disconnect);
export const useGitHubInstallation = (): EphemeralMutationFor<
  typeof GitConnectionService.installation
> => useEphemeralMutation(GitConnectionService.installation);

export function useRefreshGitConnections(): () => Promise<void> {
  const client = useQueryClient();
  return async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["git-connections"] }),
      client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEYS.all }),
      client.invalidateQueries({ queryKey: ["gitlab-resources"] }),
      client.invalidateQueries({
        predicate: (query) => {
          const key = String(query.queryKey[0]);
          return (
            (key === "user" && query.queryKey[1] !== "authenticated") ||
            /repositor|branch|installation|git-organization/.test(key)
          );
        },
      }),
    ]);
  };
}
