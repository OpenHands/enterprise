import { openHands } from "./open-hands-axios";
import {
  MarketplaceRegistration,
  MarketplacePluginInfo,
  SkillInfo,
  SkillNameConflict,
} from "#/types/settings";

interface SkillPage {
  items: SkillInfo[];
  next_page_id: string | null;
}

export interface MarketplaceSkillsResponse {
  skills: SkillInfo[];
  plugins: MarketplacePluginInfo[];
  marketplace_skills: Record<string, string[]>;
  errors: string[];
  conflicts?: SkillNameConflict[];
}

export interface MarketplaceCheckResponse {
  status: "ok" | "error" | "unchecked";
  reason:
    | "not_found"
    | "ref_not_found"
    | "path_not_found"
    | "empty"
    | "unsupported"
    | "sandbox_folder"
    | "unknown_host"
    | "failed"
    | null;
  plugin_count: number;
  skill_count: number;
  // How a repository that passed will be read: through its marketplace file,
  // as a plain folder of skills, or as a plugin whose skills alone will load.
  layout?: "marketplace" | "skills" | "plugin" | null;
}

class SkillsService {
  /**
   * Search available skills (global + user skills) with pagination
   */
  static async getSkills(): Promise<SkillInfo[]> {
    const { data } = await openHands.get<SkillPage>("/api/v1/skills/search", {
      params: { limit: 100 },
    });
    return data.items;
  }

  /**
   * Get skills from marketplace repositories
   *
   * This endpoint fetches and returns skill metadata from marketplace repos
   * without requiring an active sandbox session.
   */
  static async getMarketplaceSkills(
    marketplaces: MarketplaceRegistration[],
  ): Promise<MarketplaceSkillsResponse> {
    const { data } = await openHands.post<MarketplaceSkillsResponse>(
      "/api/v1/skills/marketplace-skills",
      marketplaces,
    );
    return data;
  }

  /**
   * Check that a marketplace will load in conversations, before saving it
   */
  static async checkMarketplace(
    marketplace: MarketplaceRegistration,
  ): Promise<MarketplaceCheckResponse> {
    const { data } = await openHands.post<MarketplaceCheckResponse>(
      "/api/v1/skills/marketplace-check",
      marketplace,
    );
    return data;
  }
}

export default SkillsService;
