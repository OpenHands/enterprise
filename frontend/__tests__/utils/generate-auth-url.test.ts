import { describe, expect, it } from "vitest";
import { generateAuthUrl } from "#/utils/generate-auth-url";

const requestUrl = new URL("https://app.example.com/login?next=canvas");

describe("generateAuthUrl realm configuration", () => {
  it.each(["auth.example.com", "https://auth.example.com/"])(
    "retains the legacy realm for %s",
    (authUrl) => {
      const url = new URL(generateAuthUrl("enterprise_sso", requestUrl, authUrl));
      expect(url.pathname).toBe("/realms/allhands/protocol/openid-connect/auth");
      expect(url.origin).toBe("https://auth.example.com");
    },
  );

  it.each([
    "https://auth.example.com/realms/allhands-oidc",
    "https://auth.example.com/realms/allhands-oidc/",
  ])("uses the explicit realm for %s", (authUrl) => {
    const url = new URL(generateAuthUrl("enterprise_sso", requestUrl, authUrl));
    expect(url.pathname).toBe("/realms/allhands-oidc/protocol/openid-connect/auth");
    expect(url.searchParams.get("client_id")).toBe("allhands");
    expect(url.searchParams.get("kc_idp_hint")).toBe("enterprise_sso");
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://app.example.com/oauth/keycloak/callback",
    );
    expect(url.searchParams.get("scope")).toBe("openid email profile");
    expect(url.searchParams.get("state")).toContain("login_method=enterprise_sso");
  });

  it("retains a Keycloak server path prefix", () => {
    const url = new URL(
      generateAuthUrl("github", requestUrl, "https://auth.example.com/auth"),
    );
    expect(url.pathname).toBe("/auth/realms/allhands/protocol/openid-connect/auth");
  });
});
