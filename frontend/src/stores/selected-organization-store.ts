import { create } from "zustand";
import { devtools } from "zustand/middleware";

interface SelectedOrganizationState {
  organizationId: string | null;
  /**
   * True once a super admin has deliberately picked "All Organizations"
   * in the {@link OrgSelector}. `organizationId` is `null` both before any
   * organization has loaded *and* in this deliberate state, so
   * `useAutoSelectOrganization` relies on this flag to tell "nothing
   * selected yet" apart from "explicitly viewing every organization" --
   * without it, auto-select would immediately re-pick the first org.
   */
  explicitlyNoOrg: boolean;
}

interface SelectedOrganizationActions {
  setOrganizationId: (orgId: string | null) => void;
  /** Deliberately clear the selection to browse the all-users admin view. */
  clearOrganizationId: () => void;
}

type SelectedOrganizationStore = SelectedOrganizationState &
  SelectedOrganizationActions;

const initialState: SelectedOrganizationState = {
  organizationId: null,
  explicitlyNoOrg: false,
};

export const useSelectedOrganizationStore = create<SelectedOrganizationStore>()(
  devtools(
    (set) => ({
      ...initialState,
      setOrganizationId: (organizationId) =>
        set({ organizationId, explicitlyNoOrg: false }),
      clearOrganizationId: () =>
        set({ organizationId: null, explicitlyNoOrg: true }),
    }),
    { name: "SelectedOrganizationStore" },
  ),
);

export const getSelectedOrganizationIdFromStore = (): string | null =>
  useSelectedOrganizationStore.getState().organizationId;
