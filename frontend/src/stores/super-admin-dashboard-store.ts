import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export const DEFAULT_SUPER_ADMIN_DASHBOARD_TIME_WINDOW = "30d";

interface SuperAdminDashboardState {
  /** Organizations the dashboard is filtered to; empty means all of them. */
  selectedOrgIds: string[];
  timeWindow: string;
}

interface SuperAdminDashboardActions {
  setSelectedOrgIds: (selectedOrgIds: string[]) => void;
  setTimeWindow: (timeWindow: string) => void;
}

/**
 * The Super Admin Dashboard's organization filter and time window. Kept for
 * the browser tab, so leaving the dashboard and coming back (or reloading)
 * keeps them, while a new tab starts from all organizations.
 */
export const useSuperAdminDashboardStore = create<
  SuperAdminDashboardState & SuperAdminDashboardActions
>()(
  persist(
    (set) => ({
      selectedOrgIds: [],
      timeWindow: DEFAULT_SUPER_ADMIN_DASHBOARD_TIME_WINDOW,
      setSelectedOrgIds: (selectedOrgIds) => set({ selectedOrgIds }),
      setTimeWindow: (timeWindow) => set({ timeWindow }),
    }),
    {
      name: "super-admin-dashboard",
      storage: createJSONStorage(() => sessionStorage),
      partialize: ({ selectedOrgIds, timeWindow }) => ({
        selectedOrgIds,
        timeWindow,
      }),
    },
  ),
);
