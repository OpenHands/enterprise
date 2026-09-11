// mcp-service.types.ts
// Types for the /api/v1/mcp endpoints.

/**
 * Body for `POST /api/v1/mcp/test`. `server` is a single entry in the SDK
 * `mcp_config` map shape (as produced by `toSdkMcpConfig`); the backend
 * restores redacted secrets from the stored server of the same `name`.
 */
export interface MCPTestRequest {
  name?: string;
  server: Record<string, unknown>;
  timeout?: number;
}

export type MCPTestFailureKind = "timeout" | "connection" | "unknown";

export interface MCPTestSuccess {
  ok: true;
  tools: string[];
}

export interface MCPTestFailure {
  ok: false;
  error: string;
  error_kind: MCPTestFailureKind;
}

export type MCPTestResponse = MCPTestSuccess | MCPTestFailure;
