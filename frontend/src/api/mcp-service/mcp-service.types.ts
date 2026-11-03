// mcp-service.types.ts
// Types for the /api/v1/mcp endpoints.

import type { MCPJsonValue } from "#/types/settings";

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

/** `POST /api/v1/mcp/oauth/start`: opens a consent flow for a remote server. */
export interface MCPOAuthStartResponse {
  ok: boolean;
  job_id?: string | null;
  authorization_url?: string | null;
  error?: string | null;
  error_kind?: string | null;
}

/**
 * `GET /api/v1/mcp/oauth/status/{job_id}`. On success `oauth_state` carries
 * the tokens to store in a member's own server's `auth.state`. A job started
 * for an organization listing omits them: the org routes take them from the
 * job by its id.
 */
export interface MCPOAuthStatusResponse {
  ok: boolean;
  status: "pending" | "authorizing" | "succeeded" | "failed";
  job_id: string;
  authorization_url?: string | null;
  callback_ready?: boolean;
  tools?: string[] | null;
  oauth_state?: Record<string, MCPJsonValue> | null;
  error?: string | null;
  error_kind?: string | null;
}
