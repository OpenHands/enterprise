import { useNavigate } from "react-router";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useSwitchOrganization } from "#/hooks/mutation/use-switch-organization";
import { setSelectedOrg } from "#/utils/local-storage";

export function useSuperAdminOpenOrg() {
  const navigate = useNavigate();
  const { organizationId, setOrganizationId } = useSelectedOrganizationId();
  const { mutate: switchOrganization } = useSwitchOrganization();

  return (orgId: string, orgName: string) => {
    if (organizationId !== orgId) {
      setOrganizationId(orgId, { skipRevalidation: true });
      setSelectedOrg(orgId);
      switchOrganization({ orgId, orgName, isPersonal: false });
    }
    navigate("/settings/usage-monitoring");
  };
}
