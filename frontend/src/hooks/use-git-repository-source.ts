import { Provider } from "#/types/settings";
import { shouldUseInstallationRepos } from "#/utils/utils";
import { useConfig } from "./query/use-config";
import { useGitConnections } from "./query/use-git-connections";

/** GitHub PATs list the user's repos directly, even in SaaS application mode. */
export function useGitRepositorySource(provider: Provider | null): {
  useInstallationRepos: boolean;
  isReady: boolean;
} {
  const { data: config } = useConfig();
  const connections = useGitConnections();
  const nativeGitHub = config?.auth_mode === "native" && provider === "github";
  const connection = connections.data?.connections.find(
    (item) => item.provider === provider,
  );
  return {
    useInstallationRepos: nativeGitHub
      ? connection?.auth_type === "oauth" &&
        connections.data?.capabilities.github?.installation_available === true
      : !!provider && shouldUseInstallationRepos(provider, config?.app_mode),
    isReady: !nativeGitHub || connections.isSuccess,
  };
}
