import { useQuery, UseQueryResult } from "@tanstack/react-query";
import {
  NativeAuthService,
  NativeProfile,
} from "#/api/native-auth-service/native-auth-service.api";
import { useConfig } from "./use-config";
import { useIsAuthed } from "./use-is-authed";

export function useNativeProfile(): UseQueryResult<NativeProfile> {
  const { data: config } = useConfig();
  const { data: isAuthed } = useIsAuthed();
  return useQuery({
    queryKey: ["native-profile"],
    queryFn: NativeAuthService.profile,
    enabled: config?.auth_mode === "native" && isAuthed === true,
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
