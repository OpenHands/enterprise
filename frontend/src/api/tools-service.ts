import { openHands } from "./open-hands-axios";

/** One entry of ``GET /api/tools/catalog`` (SDK ``ToolCatalogEntry``). */
export interface ToolCatalogEntry {
  name: string;
  user_selectable: boolean;
  usable: boolean;
  description: string;
  /** Whether a profile with ``tools: null`` gets this tool. */
  in_default_set: boolean;
}

class ToolsService {
  /** The tools an agent profile can pin, built-ins included. */
  static async getToolCatalog(): Promise<ToolCatalogEntry[]> {
    const { data } = await openHands.get<{ tools: ToolCatalogEntry[] }>(
      "/api/tools/catalog",
    );
    return data.tools;
  }
}

export default ToolsService;
