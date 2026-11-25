import { useQuery } from "@tanstack/react-query";
import { scimService } from "#/api/organization-service/scim.api";

export const SCIM_SETUP_QUERY_KEY = "scim-setup";
export const SCIM_CREDENTIALS_QUERY_KEY = "scim-credentials";

/** SCIM base URL and bindable providers of the selected organization. */
export const useScimSetup = (organizationId: string) => {
  return useQuery({
    queryKey: [SCIM_SETUP_QUERY_KEY, organizationId],
    queryFn: () => scimService.getSetup(organizationId),
  });
};

/** SCIM credential metadata of the selected organization. */
export const useScimCredentials = (organizationId: string) => {
  return useQuery({
    queryKey: [SCIM_CREDENTIALS_QUERY_KEY, organizationId],
    queryFn: () => scimService.listCredentials(organizationId),
  });
};
