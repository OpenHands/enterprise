import McpService from "#/api/mcp-service/mcp-service.api";
import type { MCPOAuthStatusResponse } from "#/api/mcp-service/mcp-service.types";
import type { MCPJsonValue } from "#/types/settings";

/** Seconds to wait for the person to finish the provider's sign-in. */
const OAUTH_SIGN_IN_TIMEOUT_SECONDS = 120;

export type McpOAuthResult =
  | { ok: true; oauthState: Record<string, MCPJsonValue> | null }
  | { ok: false; error: string; errorKind: string };

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

function toResult(status: MCPOAuthStatusResponse): McpOAuthResult {
  if (status.status === "succeeded") {
    return { ok: true, oauthState: status.oauth_state ?? null };
  }
  // The backend gives up after its own budget; once it had handed out an
  // authorization URL, that budget ran out waiting for the sign-in.
  if (status.error_kind === "timeout" && status.authorization_url) {
    return {
      ok: false,
      error: status.error || "OAuth authorization was not completed",
      errorKind: "authorization",
    };
  }
  return {
    ok: false,
    error: status.error || "OAuth authorization failed",
    errorKind: status.error_kind || "unknown",
  };
}

/**
 * Authorize a remote MCP server through its OAuth flow (OHE-3870): opens the
 * provider's sign-in in a new window, waits for the app server to receive the
 * callback, and returns the OAuth state to store in the server's
 * `auth.state`. The window is opened synchronously so popup blockers allow
 * it; call this straight from the click or submit handler.
 */
export async function authorizeMcpOAuth({
  name,
  server,
}: {
  name: string;
  server: Record<string, unknown>;
}): Promise<McpOAuthResult> {
  let popup: Window | null = window.open("about:blank", "_blank");
  try {
    const start = await McpService.startOAuth({
      name,
      server,
      timeout: OAUTH_SIGN_IN_TIMEOUT_SECONDS,
    });
    if (!start.ok || !start.job_id) {
      popup?.close();
      return {
        ok: false,
        error: start.error || "Could not start OAuth authorization",
        errorKind: start.error_kind || "unknown",
      };
    }
    // No authorization URL: the server accepted stored tokens, so no consent
    // is needed and the outcome is read from the status route.
    if (!start.authorization_url) {
      popup?.close();
      popup = null;
    }

    let status = await McpService.getOAuthStatus(start.job_id);
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (status.status === "succeeded" || status.status === "failed") {
        popup?.close();
        return toResult(status);
      }
      if (status.callback_ready) break;
      // eslint-disable-next-line no-await-in-loop
      await sleep(250);
      // eslint-disable-next-line no-await-in-loop
      status = await McpService.getOAuthStatus(start.job_id);
    }

    if (popup && start.authorization_url) {
      popup.location.href = start.authorization_url;
    }

    for (
      let attempt = 0;
      attempt < OAUTH_SIGN_IN_TIMEOUT_SECONDS;
      attempt += 1
    ) {
      // eslint-disable-next-line no-await-in-loop
      await sleep(1000);
      // eslint-disable-next-line no-await-in-loop
      status = await McpService.getOAuthStatus(start.job_id);
      if (status.status === "succeeded" || status.status === "failed") {
        popup?.close();
        return toResult(status);
      }
    }

    return start.authorization_url
      ? {
          ok: false,
          error: "OAuth authorization was not completed",
          errorKind: "authorization",
        }
      : {
          ok: false,
          error: "OAuth authorization timed out",
          errorKind: "timeout",
        };
  } catch (error) {
    popup?.close();
    throw error;
  }
}
