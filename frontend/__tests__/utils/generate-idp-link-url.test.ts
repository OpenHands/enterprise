import { afterEach, describe, expect, it } from "vitest";
import { generateIdpLinkUrl } from "#/utils/generate-idp-link-url";
import { generateAuthUrl } from "#/utils/generate-auth-url";
import { LoginMethod, setLoginMethod } from "#/utils/local-storage";

afterEach(() => localStorage.clear());

describe("backend authorization URLs", () => {
  it("links a repository provider through the backend, independently of the login provider", () => {
    setLoginMethod(LoginMethod.ENTERPRISE_SSO);
    const url = new URL(
      generateIdpLinkUrl(
        "github",
        new URL("https://app.example.com/settings/integrations?tab=git"),
      ),
    );
    expect(`${url.origin}${url.pathname}`).toBe(
      "https://app.example.com/api/auth/providers/github/link",
    );
    expect(url.searchParams.get("redirect_url")).toBe(
      "/settings/integrations?tab=git",
    );
    expect(url.searchParams.has("kc_idp_hint")).toBe(false);
    expect(url.searchParams.has("state")).toBe(false);
  });

  it("preserves device return and invitation context for backend-owned login", () => {
    localStorage.setItem("openhands_invitation_token", "invitation");
    const url = new URL(
      generateAuthUrl(
        "enterprise_sso",
        new URL(
          "https://app.example.com/login?returnTo=%2Foauth%2Fdevice%2Fverify%3Fuser_code%3DAAAA-BBBB",
        ),
      ),
    );
    expect(url.pathname).toBe("/api/auth/authorize");
    expect(url.searchParams.get("provider")).toBe("enterprise_sso");
    expect(url.searchParams.get("redirect_url")).toBe(
      "/oauth/device/verify?user_code=AAAA-BBBB",
    );
    expect(url.searchParams.get("invitation_token")).toBe("invitation");
  });
});
