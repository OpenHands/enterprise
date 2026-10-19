import { describe, expect, it } from "vitest";
import { shouldUseInstallationRepos } from "#/utils/utils";

describe("shouldUseInstallationRepos", () => {
  it.each(["github", "azure_devops"] as const)(
    "uses installations for %s only in SaaS mode",
    (provider) => {
      expect(shouldUseInstallationRepos(provider, "saas")).toBe(true);
      expect(shouldUseInstallationRepos(provider, "oss")).toBe(false);
      expect(shouldUseInstallationRepos(provider, undefined)).toBe(false);
    },
  );

  it("always uses installations for Bitbucket and never for GitLab", () => {
    expect(shouldUseInstallationRepos("bitbucket", "oss")).toBe(true);
    expect(shouldUseInstallationRepos("bitbucket_data_center", "saas")).toBe(
      true,
    );
    expect(shouldUseInstallationRepos("gitlab", "saas")).toBe(false);
  });
});
