// mcp-service.types.ts
// Types for the /api/v1/mcp endpoints.

/**
 * Body for `POST /api/v1/mcp/test` (mirrors the backend `MCPTestRequestBody`).
 * `server` is a single entry in the SDK `mcp_config` map shape (as produced by
 * `toSdkMcpConfig`) and is intentionally untyped on both sides: the backend
 * restores redacted secrets from the stored server of the same `name` before
 * validating it.
 */
export interface MCPTestRequest {
  /** Settings key of the server; defaults to "test-server" on the backend. */
  name?: string;
  server: Record<string, unknown>;
  /** Seconds to wait for connection + tools/list (backend default 15, max 120). */
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
