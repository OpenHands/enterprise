import { organizationService } from "#/api/organization-service/organization-service.api";
import { WebClientFeatureFlags } from "#/api/option-service/option.types";
import { queryClient } from "#/query-client-config";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { OrganizationsQueryData } from "#/types/org";
import { setSelectedOrg } from "#/utils/local-storage";

/**
 * Query param external apps (e.g. agent-canvas) append to settings links so
 * the page opens on the org the user is working in there instead of the
 * cloud's last-used org.
 */
export const ORG_QUERY_PARAM = "org";

/**
 * True while a request still carries `?org=`. The settings loader consumes
 * the param and redirects without it; child route guards must not redirect
 * (and drop the param) in the meantime.
 */
export const hasPendingOrgSwitch = (request: Request): boolean =>
  new URL(request.url).searchParams.has(ORG_QUERY_PARAM);

/**
 * Make `orgId` the user's current organization, server-side and locally.
 *
 * Returns false when the org list could not be fetched (e.g. unauthenticated)
 * so the caller leaves the param in place and it survives the login redirect.
 * Returns true once the param has been dealt with, whether or not a switch
 * was needed or possible.
 */
export const switchOrganizationFromUrl = async (
  orgId: string,
  featureFlags: WebClientFeatureFlags | undefined,
): Promise<boolean> => {
  let organizationsData: OrganizationsQueryData;
  try {
    organizationsData = await queryClient.fetchQuery<OrganizationsQueryData>({
      queryKey: ["organizations"],
      queryFn: organizationService.getOrganizations,
      staleTime: 1000 * 60 * 5, // 5 minutes - matches useOrganizations hook
    });
  } catch {
    return false;
  }

  if (organizationsData.currentOrgId === orgId) return true;

  const target = organizationsData.items.find((org) => org.id === orgId);
  if (!target) return true;
  // Org-only installs hide personal workspaces (see useOrganizations).
  if (target.is_personal && featureFlags?.hide_personal_workspaces) return true;

  try {
    await organizationService.switchOrganization({ orgId });
  } catch {
    return true;
  }

  queryClient.setQueryData<OrganizationsQueryData>(["organizations"], (old) =>
    old ? { ...old, currentOrgId: orgId } : old,
  );
  useSelectedOrganizationStore.getState().setOrganizationId(orgId);
  // Broadcast org change to other apps (e.g. Automations) via localStorage
  setSelectedOrg(orgId);
  return true;
};
