import { describe, expect, it } from "vitest";
import { getProviderSetupLinks } from "#/components/features/super-admin/auth-provider-setup-links";

describe("getProviderSetupLinks", () => {
  it("links GitHub to the OAuth app pages on github.com when no URL is saved", () => {
    expect(getProviderSetupLinks("github", ["", null])).toEqual([
      {
        kind: "create",
        url: "https://github.com/settings/applications/new",
        service: "GitHub",
      },
      {
        kind: "list",
        url: "https://github.com/settings/developers",
        service: "GitHub",
      },
    ]);
  });

  it("follows the entered authorization URL for GitHub Enterprise Server", () => {
    const links = getProviderSetupLinks("github", [
      "https://ghe.example.com/login/oauth/authorize",
      "https://github.com/login/oauth/access_token",
    ]);
    expect(links.map((l) => l.url)).toEqual([
      "https://ghe.example.com/settings/applications/new",
      "https://ghe.example.com/settings/developers",
    ]);
  });

  it("uses the token URL when the authorization URL is empty or a placeholder", () => {
    const tokenUrl = "https://gitlab.corp.io/oauth/token";
    expect(getProviderSetupLinks("gitlab", ["", tokenUrl])[0].url).toBe(
      "https://gitlab.corp.io/-/user_settings/applications",
    );
    expect(
      getProviderSetupLinks("gitlab", [
        "https://PLACEHOLDER.COM/oauth/authorize",
        tokenUrl,
      ])[0].url,
    ).toBe("https://gitlab.corp.io/-/user_settings/applications");
  });

  it("falls back to the brand default for unparsable or placeholder URLs", () => {
    expect(getProviderSetupLinks("github", ["not a url"])[0].url).toBe(
      "https://github.com/settings/applications/new",
    );
    expect(
      getProviderSetupLinks("gitlab", [
        "https://PLACEHOLDER.COM/oauth/authorize",
      ])[0].url,
    ).toBe("https://gitlab.com/-/user_settings/applications");
  });

  it("links GitLab user and admin application pages", () => {
    expect(
      getProviderSetupLinks("gitlab", [
        "https://gitlab.corp.io/oauth/authorize",
      ]),
    ).toEqual([
      {
        kind: "list",
        url: "https://gitlab.corp.io/-/user_settings/applications",
        service: "GitLab",
      },
      {
        kind: "admin",
        url: "https://gitlab.corp.io/admin/applications",
        service: "GitLab",
      },
    ]);
  });

  it("builds no link for a brand with no default host and only placeholder URLs", () => {
    expect(
      getProviderSetupLinks("forgejo", [
        "https://YOUR_FORGEJO_HOST/login/oauth/authorize",
      ]),
    ).toEqual([]);
  });

  it("names the external service on every link", () => {
    expect(getProviderSetupLinks("bitbucket", [])).toEqual([
      expect.objectContaining({ kind: "docs", service: "Atlassian" }),
    ]);
    expect(getProviderSetupLinks("bitbucket_data_center", [])).toEqual([
      expect.objectContaining({ kind: "docs", service: "Atlassian" }),
    ]);
    expect(getProviderSetupLinks("azure_devops", [])).toEqual([
      expect.objectContaining({ kind: "list", service: "Microsoft Entra" }),
      expect.objectContaining({ kind: "docs", service: "Microsoft Learn" }),
    ]);
  });

  it("has no links for generic enterprise SSO", () => {
    expect(getProviderSetupLinks("enterprise_sso", [])).toEqual([]);
  });
});
