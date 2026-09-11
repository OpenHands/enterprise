// mcp-service.api.ts
// This file contains API methods for /api/v1/mcp endpoints.

import { openHands } from "../open-hands-axios";
import type { MCPTestRequest, MCPTestResponse } from "./mcp-service.types";

class McpService {
  /**
   * Test a remote MCP server configuration without persisting it.
   * Calls the /api/v1/mcp/test endpoint. Connection and timeout failures
   * come back as HTTP 200 with `ok: false`; invalid or stdio configs are 422.
   */
  static async testServer(request: MCPTestRequest): Promise<MCPTestResponse> {
    const { data } = await openHands.post<MCPTestResponse>(
      "/api/v1/mcp/test",
      request,
    );
    return data;
  }
}

export default McpService;
