import { useQuery, UseQueryResult } from "@tanstack/react-query";
import {
  NativeAuthService,
  NativeInvitationOrganization,
  NativeAccount,
  NativeInvitation,
  NativeRole,
  PaginatedResponse,
} from "#/api/native-auth-service/native-auth-service.api";
import { useCanManageUsers } from "./use-native-profile";

export function useNativeAccounts(
  offset: number,
): UseQueryResult<PaginatedResponse<NativeAccount>> {
  const { canManageUsers } = useCanManageUsers();
  return useQuery({
    queryKey: ["native-accounts", offset],
    queryFn: () => NativeAuthService.accounts(offset),
    enabled: canManageUsers,
  });
}
export function useNativeAccount(
  id: string | null,
): UseQueryResult<NativeAccount> {
  const { canManageUsers } = useCanManageUsers();
  return useQuery({
    queryKey: ["native-accounts", "detail", id],
    queryFn: (): Promise<NativeAccount> => {
      if (!id) throw new Error("An account ID is required.");
      return NativeAuthService.account(id);
    },
    enabled: canManageUsers && !!id,
  });
}
export function useNativeInvitations(
  offset: number,
): UseQueryResult<PaginatedResponse<NativeInvitation>> {
  const { canManageUsers } = useCanManageUsers();
  return useQuery({
    queryKey: ["native-invitations", offset],
    queryFn: () => NativeAuthService.invitations(offset),
    enabled: canManageUsers,
  });
}
export function useNativeRoles(): UseQueryResult<NativeRole[]> {
  const { canManageUsers } = useCanManageUsers();
  return useQuery({
    queryKey: ["native-roles"],
    queryFn: NativeAuthService.roles,
    enabled: canManageUsers,
  });
}

async function fetchInvitationOrganizations(
  organizations: NativeInvitationOrganization[] = [],
): Promise<NativeInvitationOrganization[]> {
  const page = await NativeAuthService.invitationOrganizations(
    organizations.length,
  );
  const combined = [...organizations, ...page.items];
  if (page.items.length === 0 || combined.length >= page.total) return combined;
  return fetchInvitationOrganizations(combined);
}

export function useNativeInvitationOrganizations(): UseQueryResult<
  NativeInvitationOrganization[]
> {
  const { canManageUsers } = useCanManageUsers();
  return useQuery({
    queryKey: ["native-invitation-organizations"],
    queryFn: (): Promise<NativeInvitationOrganization[]> =>
      fetchInvitationOrganizations(),
    enabled: canManageUsers,
  });
}
