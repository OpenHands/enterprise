import { useQuery, UseQueryResult } from "@tanstack/react-query";
import { useAuthentication } from "#/hooks/use-authentication";
import {
  NativeAuthService,
  NativeProfile,
} from "#/api/native-auth-service/native-auth-service.api";
import { useIsAuthed } from "./use-is-authed";

export function useNativeProfile(): UseQueryResult<NativeProfile> {
  const authentication = useAuthentication();
  const { data: isAuthed } = useIsAuthed();
  return useQuery({
    queryKey: ["native-profile"],
    queryFn: NativeAuthService.profile,
    enabled:
      authentication.accountActions.includes("profile") && isAuthed === true,
  });
}

export function useCanManageUsers(): UseQueryResult<NativeProfile> & {
  canManageUsers: boolean;
} {
  const profile = useNativeProfile();
  return {
    ...profile,
    canManageUsers:
      profile.data?.global_permissions?.includes("manage_users") === true,
  };
}
