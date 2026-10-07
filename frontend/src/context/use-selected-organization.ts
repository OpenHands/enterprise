import { useRevalidator } from "react-router";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

interface SetOrganizationIdOptions {
  /** Skip route revalidation. Useful for initial auto-selection to avoid duplicate API calls. */
  skipRevalidation?: boolean;
}

export const useSelectedOrganizationId = () => {
  const revalidator = useRevalidator();
  const {
    organizationId,
    explicitlyNoOrg,
    setOrganizationId: setOrganizationIdStore,
    clearOrganizationId: clearOrganizationIdStore,
  } = useSelectedOrganizationStore();

  const setOrganizationId = (
    newOrganizationId: string | null,
    options?: SetOrganizationIdOptions,
  ) => {
    setOrganizationIdStore(newOrganizationId);
    // Revalidate route to ensure the latest orgId is used.
    // This is useful for redirecting the user away from admin-only org pages.
    // Skip revalidation for initial auto-selection to avoid duplicate API calls.
    if (!options?.skipRevalidation) {
      revalidator.revalidate();
    }
  };

  /** Deliberately deselect the organization (super-admin "All Organizations" view). */
  const clearOrganizationId = (options?: SetOrganizationIdOptions) => {
    clearOrganizationIdStore();
    if (!options?.skipRevalidation) {
      revalidator.revalidate();
    }
  };

  return {
    organizationId,
    explicitlyNoOrg,
    setOrganizationId,
    clearOrganizationId,
  };
};
