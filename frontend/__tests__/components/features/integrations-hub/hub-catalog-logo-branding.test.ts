import { describe, expect, it } from "vitest";
import {
  DEFAULT_HUB_CATALOG_LOGO_BG,
  getHubCatalogLogoBranding,
} from "#/components/features/integrations-hub/hub-catalog-logo-branding";
import { OFFICIAL_HUB_CATALOG } from "#/components/features/integrations-hub/hub-official-catalog";

describe("hub catalog logo branding", () => {
  it("uses Hub brand tiles for MCP and OAuth catalog entries", () => {
    expect(getHubCatalogLogoBranding("slack")).toEqual({
      iconBg: "#4A154B",
      iconColor: "#FFFFFF",
    });
    expect(getHubCatalogLogoBranding("notion")).toEqual({
      iconBg: "#FFFFFF",
      iconColor: "#000000",
    });
    expect(getHubCatalogLogoBranding("github")).toEqual({
      iconBg: "#6E40C9",
      iconColor: "#FFFFFF",
    });
    expect(getHubCatalogLogoBranding("google-docs").iconBg).toBe("#4285F4");
    expect(getHubCatalogLogoBranding("vanta").iconBg).toBe("#240642");
  });

  it("covers every official catalog slug", () => {
    for (const entry of OFFICIAL_HUB_CATALOG) {
      expect(getHubCatalogLogoBranding(entry.slug).iconBg, entry.slug).not.toBe(
        DEFAULT_HUB_CATALOG_LOGO_BG,
      );
    }
  });

  it("falls back to the default tile for unknown slugs", () => {
    expect(getHubCatalogLogoBranding("unknown-provider")).toEqual({
      iconBg: DEFAULT_HUB_CATALOG_LOGO_BG,
      iconColor: "#FFFFFF",
    });
  });
});
