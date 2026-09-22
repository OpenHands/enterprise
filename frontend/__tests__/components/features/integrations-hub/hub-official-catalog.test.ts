import { describe, expect, it } from "vitest";
import { OFFICIAL_HUB_CATALOG } from "#/components/features/integrations-hub/hub-official-catalog";
import { mockCatalogIntegrations } from "#/components/features/integrations-hub/integrations-hub-mock";

describe("official Hub catalog", () => {
  it("includes the full curated provider catalog", () => {
    const slugs = OFFICIAL_HUB_CATALOG.map((item) => item.slug);
    expect(slugs).toHaveLength(80);
    expect(new Set(slugs).size).toBe(80);
    expect(slugs).toEqual(
      expect.arrayContaining([
        "github",
        "gitlab",
        "azure_devops",
        "forgejo",
        "bitbucket_data_center",
        "jira-dc",
        "slack",
        "notion",
        "elevenlabs",
        "google-calendar",
        "datadog",
      ]),
    );
  });

  it("keeps detailed tools for registered stub connectors", () => {
    const catalog = mockCatalogIntegrations();
    expect(catalog).toHaveLength(80);
    const slack = catalog.find((item) => item.slug === "slack");
    expect(slack?.connected).toBe(true);
    expect(slack?.tools.length).toBeGreaterThan(0);
    expect(slack?.logoUrl).toContain("slack");
    expect(slack?.serverUrl).toBe("https://mcp.slack.com/mcp");
    expect(slack?.notes).toContain("hosted MCP");
    expect(catalog.find((item) => item.slug === "elevenlabs")?.connected).toBe(
      false,
    );
  });
});
