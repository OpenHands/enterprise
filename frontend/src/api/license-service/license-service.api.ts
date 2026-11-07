import { openHands } from "#/api/open-hands-axios";

export type LicenseState =
  "disabled" | "missing_key" | "pending" | "valid" | "invalid" | "unverified";

export interface LicenseStatus {
  state: LicenseState;
  reason: string | null;
  // Whether the install uses an offline license file.
  offline: boolean;
  // trial, paid or development.
  type: string | null;
  // Null when the license never expires or the date isn't known.
  license_ends_at: string | null;
  expires_at: string | null;
  last_checked_at: string | null;
  customer_center_url: string;
}

export const licenseService = {
  getStatus: async (): Promise<LicenseStatus> => {
    const { data } = await openHands.get<LicenseStatus>("/api/license/status");
    return data;
  },
};
