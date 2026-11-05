// mcp-service.api.ts
// This file contains API methods for /api/v1/mcp endpoints.

import { openHands } from "../open-hands-axios";
import type {
  MCPOAuthStartResponse,
  MCPOAuthStatusResponse,
  MCPTestRequest,
  MCPTestResponse,
} from "./mcp-service.types";

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

  /**
   * Start the OAuth flow for a remote MCP server and get the provider's
   * authorization URL to open in the browser. Validated like `testServer`.
   */
  static async startOAuth(
    request: MCPTestRequest,
  ): Promise<MCPOAuthStartResponse> {
    const { data } = await openHands.post<MCPOAuthStartResponse>(
      "/api/v1/mcp/oauth/start",
      request,
    );
    return data;
  }

  /** Progress of a flow started by the current user. */
  static async getOAuthStatus(jobId: string): Promise<MCPOAuthStatusResponse> {
    const { data } = await openHands.get<MCPOAuthStatusResponse>(
      `/api/v1/mcp/oauth/status/${encodeURIComponent(jobId)}`,
    );
    return data;
  }
}

export default McpService;
