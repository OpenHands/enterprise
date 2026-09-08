import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { generateIdpLinkUrl } from "#/utils/generate-idp-link-url";
import { LoginMethod, setLoginMethod } from "#/utils/local-storage";

describe("generateIdpLinkUrl", () => {
  const requestUrl = new URL(
    "https://app.example.com/settings/integrations?tab=git",
  );

  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("should start a Keycloak idp_link action hinted at the stored login method", () => {
    // Arrange
    setLoginMethod(LoginMethod.ENTERPRISE_SSO);

    // Act
    const url = new URL(
      generateIdpLinkUrl("github", requestUrl, "auth.example.com"),
    );

    // Assert
    expect(`${url.origin}${url.pathname}`).toBe(
      "https://auth.example.com/realms/allhands/protocol/openid-connect/auth",
    );
    expect(url.searchParams.get("kc_action")).toBe("idp_link:github");
    expect(url.searchParams.get("kc_idp_hint")).toBe("enterprise_sso");
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://app.example.com/oauth/keycloak/callback",
    );
    expect(JSON.parse(atob(url.searchParams.get("state")!))).toEqual({
      redirect_url: "https://app.example.com/settings/integrations",
      link_provider: "github",
    });
  });

  it("should omit the identity provider hint when no login method is stored", () => {
    // Act
    const url = new URL(
      generateIdpLinkUrl("gitlab", requestUrl, "auth.example.com"),
    );

    // Assert
    expect(url.searchParams.has("kc_idp_hint")).toBe(false);
    expect(url.searchParams.get("kc_action")).toBe("idp_link:gitlab");
  });
});
