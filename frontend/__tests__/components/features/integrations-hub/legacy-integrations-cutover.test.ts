import { describe, expect, it } from "vitest";
import {
  buildLegacyCutoverItems,
  hubCatalogSlugSet,
  hubConnectedSlugSet,
  legacyCutoverDisplayName,
} from "#/components/features/integrations-hub/legacy-integrations-cutover";

describe("legacy-integrations-cutover", () => {
  it("lists legacy providers that are not yet connected in Hub", () => {
    const items = buildLegacyCutoverItems(
      ["github", "gitlab", "enterprise_sso"],
      hubConnectedSlugSet([]),
      hubCatalogSlugSet([
        { slug: "github" },
        { slug: "gitlab" },
        { slug: "bitbucket" },
      ]),
    );

    expect(items).toEqual([
      {
        id: "github",
        hubSlug: "github",
        canReconnectInHub: true,
      },
      {
        id: "gitlab",
        hubSlug: "gitlab",
        canReconnectInHub: true,
      },
    ]);
  });

  it("omits providers already connected in Hub", () => {
    const items = buildLegacyCutoverItems(
      ["github", "bitbucket"],
      hubConnectedSlugSet([{ slug: "github", connected: true }]),
      hubCatalogSlugSet([{ slug: "github" }, { slug: "bitbucket" }]),
    );

    expect(items).toEqual([
      {
        id: "bitbucket",
        hubSlug: "bitbucket",
        canReconnectInHub: true,
      },
    ]);
  });

  it("dedupes providers and maps Bitbucket DC to its Hub slug", () => {
    const items = buildLegacyCutoverItems(
      ["bitbucket_data_center", "bitbucket_data_center"],
      new Set(),
      new Set(["bitbucket_data_center"]),
    );

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      id: "bitbucket_data_center",
      hubSlug: "bitbucket_data_center",
      canReconnectInHub: true,
    });
  });

  it("returns display names for known providers", () => {
    expect(legacyCutoverDisplayName("azure_devops")).toBe("Azure DevOps");
    expect(legacyCutoverDisplayName("forgejo")).toBe("Forgejo");
  });
});
