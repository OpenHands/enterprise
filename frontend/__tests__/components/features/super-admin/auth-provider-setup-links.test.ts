import { describe, expect, it } from "vitest";
import { getProviderSetupLinks } from "#/components/features/super-admin/auth-provider-setup-links";

describe("getProviderSetupLinks", () => {
  it("links GitHub to the OAuth app pages on github.com when no URL is saved", () => {
    expect(getProviderSetupLinks("github", null)).toEqual([
      { kind: "create", url: "https://github.com/settings/applications/new" },
      { kind: "list", url: "https://github.com/settings/developers" },
    ]);
  });

  it("follows the saved authorization URL for GitHub Enterprise Server", () => {
    expect(
      getProviderSetupLinks(
        "github",
        "https://ghe.example.com/login/oauth/authorize",
      ),
    ).toEqual([
      {
        kind: "create",
        url: "https://ghe.example.com/settings/applications/new",
      },
      { kind: "list", url: "https://ghe.example.com/settings/developers" },
    ]);
  });

  it("falls back to the brand default for an unparsable URL", () => {
    expect(getProviderSetupLinks("github", "not a url")[0].url).toBe(
      "https://github.com/settings/applications/new",
    );
  });

  it("links GitLab user and admin application pages on the saved host", () => {
    expect(
      getProviderSetupLinks("gitlab", "https://gitlab.corp.io/oauth/authorize"),
    ).toEqual([
      {
        kind: "list",
        url: "https://gitlab.corp.io/-/user_settings/applications",
      },
      { kind: "admin", url: "https://gitlab.corp.io/admin/applications" },
    ]);
  });

  it("does not build links from a placeholder host", () => {
    expect(
      getProviderSetupLinks(
        "forgejo",
        "https://YOUR_FORGEJO_HOST/login/oauth/authorize",
      ),
    ).toEqual([]);
    expect(
      getProviderSetupLinks(
        "gitlab",
        "https://PLACEHOLDER.COM/oauth/authorize",
      ),
    ).toEqual([
      {
        kind: "list",
        url: "https://gitlab.com/-/user_settings/applications",
      },
      { kind: "admin", url: "https://gitlab.com/admin/applications" },
    ]);
  });

  it("points Bitbucket and Azure DevOps at Atlassian / Microsoft pages", () => {
    expect(getProviderSetupLinks("bitbucket", null).map((l) => l.kind)).toEqual(
      ["docs"],
    );
    expect(
      getProviderSetupLinks("bitbucket_data_center", null).map((l) => l.kind),
    ).toEqual(["docs"]);
    expect(
      getProviderSetupLinks("azure_devops", null).map((l) => l.kind),
    ).toEqual(["list", "docs"]);
  });

  it("has no links for generic enterprise SSO", () => {
    expect(getProviderSetupLinks("enterprise_sso", null)).toEqual([]);
  });
});
