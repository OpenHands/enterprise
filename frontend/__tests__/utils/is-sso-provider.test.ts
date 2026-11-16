import { describe, expect, it } from "vitest";
import { isSsoProvider } from "#/utils/is-sso-provider";
import type { AuthProviderDetails } from "#/api/auth-providers-service/auth-providers.types";
import {
  GITHUB_PROVIDER,
  SAML_PROVIDER,
} from "../components/features/super-admin/auth-provider-fixtures";

describe("isSsoProvider", () => {
  it("counts SAML and OIDC identity providers", () => {
    expect(isSsoProvider(SAML_PROVIDER.details)).toBe(true);
    expect(
      isSsoProvider({
        ...GITHUB_PROVIDER.details,
        provider_type: "enterprise_sso",
        legacy_keycloak_type: null,
      } as AuthProviderDetails),
    ).toBe(true);
  });

  it("doesn't count git providers or Keycloak's own rows", () => {
    expect(isSsoProvider(GITHUB_PROVIDER.details)).toBe(false);
    expect(
      isSsoProvider({
        ...GITHUB_PROVIDER.details,
        provider_type: "enterprise_sso",
        legacy_keycloak_type: "github",
      } as AuthProviderDetails),
    ).toBe(false);
  });
});
