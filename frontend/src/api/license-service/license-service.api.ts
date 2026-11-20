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
  // What the install may use now. Every feature is on while enforcement is
  // off, and off without a valid license.
  features: { sso: boolean };
}

export interface LimitUsage {
  used: number;
  // Null means unlimited.
  limit: number | null;
}

export interface LicenseUsage {
  users: LimitUsage;
  // Team organizations. Personal workspaces don't count.
  orgs: LimitUsage;
  customer_center_url: string;
}

export interface ConfiguredLicense {
  // Where the license is set: in the install's environment, or saved by a
  // Super Admin. Null without a license.
  source: "environment" | "saved" | null;
  kind: "key" | "offline" | null;
  // The start of the license key, as Customer Center shows it.
  key_hint: string | null;
}

// A license key, or the contents of an offline license file.
export type LicenseUpdate =
  { license_key: string } | { offline_license: string };

export const licenseService = {
  getStatus: async (): Promise<LicenseStatus> => {
    const { data } = await openHands.get<LicenseStatus>("/api/license/status");
    return data;
  },
  getUsage: async (): Promise<LicenseUsage> => {
    const { data } = await openHands.get<LicenseUsage>("/api/license/usage");
    return data;
  },
  getLicense: async (): Promise<ConfiguredLicense> => {
    const { data } = await openHands.get<ConfiguredLicense>("/api/license");
    return data;
  },
  saveLicense: async (license: LicenseUpdate): Promise<ConfiguredLicense> => {
    const { data } = await openHands.put<ConfiguredLicense>(
      "/api/license",
      license,
    );
    return data;
  },
};
