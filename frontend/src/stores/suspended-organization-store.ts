import { create } from "zustand";
import { devtools } from "zustand/middleware";

export type OrganizationSuspensionReason = "organization" | "membership";

export interface OrganizationSuspension {
  /** The organization that was selected when the server refused the request. */
  orgId: string;
  reason: OrganizationSuspensionReason;
}

interface SuspendedOrganizationState {
  suspension: OrganizationSuspension | null;
}

interface SuspendedOrganizationActions {
  setSuspension: (suspension: OrganizationSuspension) => void;
}

type SuspendedOrganizationStore = SuspendedOrganizationState &
  SuspendedOrganizationActions;

const initialState: SuspendedOrganizationState = {
  suspension: null,
};

export const useSuspendedOrganizationStore =
  create<SuspendedOrganizationStore>()(
    devtools(
      (set) => ({
        ...initialState,
        setSuspension: (suspension) => set({ suspension }),
      }),
      { name: "SuspendedOrganizationStore" },
    ),
  );
