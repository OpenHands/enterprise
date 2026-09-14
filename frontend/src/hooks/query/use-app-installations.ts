import type { UseQueryResult, DefaultError } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import type { InstallationPage } from "#/types/git";
import { useGitRepositorySource } from "../use-git-repository-source";
import { useIsAuthed } from "./use-is-authed";
import GitService from "#/api/git-service/git-service.api";
import { useUserProviders } from "../use-user-providers";
import { Provider } from "#/types/settings";

/**
 * Get the first page of app installations for the provider given.
 */
export const useAppInstallations = (
  selectedProvider: Provider | null,
): UseQueryResult<InstallationPage, DefaultError> => {
  const { useInstallationRepos, isReady } =
    useGitRepositorySource(selectedProvider);
  const { data: userIsAuthenticated } = useIsAuthed();
  const { providers } = useUserProviders();

  return useQuery({
    queryKey: ["installations", providers || [], selectedProvider],
    queryFn: () => GitService.getUserInstallations(selectedProvider!),
    enabled:
      userIsAuthenticated &&
      !!selectedProvider &&
      providers.includes(selectedProvider) &&
      isReady &&
      useInstallationRepos,
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 15, // 15 minutes
  });
};
