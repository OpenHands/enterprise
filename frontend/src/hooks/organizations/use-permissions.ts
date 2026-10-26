import { useMe } from "#/hooks/query/use-me";
import { PermissionKey } from "#/utils/org/permissions";

/**
 * Permission checks for the active user in the selected organization.
 * Reads the server-computed `permissions` from `/me`, so it matches what the
 * backend enforces. While `/me` is loading, or when it has no `permissions`,
 * every check returns false (fail closed).
 */
export const usePermission = () => {
  const { data: me } = useMe();
  const permissions = me?.permissions;

  const hasPermission = (permission: PermissionKey): boolean =>
    permissions?.includes(permission) === true;

  return { hasPermission };
};
