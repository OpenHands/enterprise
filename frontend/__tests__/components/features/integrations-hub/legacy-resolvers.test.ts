import { describe, expect, it } from "vitest";
import { INTEGRATION_PROVIDER_IDS } from "#/components/features/settings/git-settings/integration-provider-icon";
import { INTEGRATIONS_HUB_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";
import {
  getLegacyResolver,
  isLegacyResolverId,
  LEGACY_RESOLVERS,
} from "#/components/features/integrations-hub/legacy-resolvers";

describe("legacy-resolvers", () => {
  it("lists every supported integration provider", () => {
    expect(LEGACY_RESOLVERS.map((resolver) => resolver.id)).toEqual([
      ...INTEGRATION_PROVIDER_IDS,
    ]);
    expect(LEGACY_RESOLVERS).toHaveLength(10);
  });

  it("builds Hub admin paths for each resolver", () => {
    expect(getLegacyResolver("github")?.path).toBe(
      `${INTEGRATIONS_HUB_PATHS.resolvers}/github`,
    );
    expect(isLegacyResolverId("jira-dc")).toBe(true);
    expect(isLegacyResolverId("unknown")).toBe(false);
  });
});
