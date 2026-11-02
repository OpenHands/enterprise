import McpService from "#/api/mcp-service/mcp-service.api";

/** The longest the backend waits for consent (`timeout` on the start request). */
const OAUTH_TIMEOUT_SECONDS = 120;
const CALLBACK_READY_ATTEMPTS = 20;
const CALLBACK_READY_POLL_MS = 250;
const STATUS_POLL_MS = 1000;

export type McpOAuthFlowFailure = {
  ok: false;
  reason: "popup_blocked" | "not_completed" | "failed";
  /** The backend's message, when it gave one. */
  error?: string;
};

export type McpOAuthFlowResult =
  | { ok: true; jobId: string }
  | McpOAuthFlowFailure;

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * Run the OAuth consent flow for an MCP server in a new window and resolve
 * with the finished job. The tokens stay on the job: the caller hands its id
 * to the route that stores them.
 *
 * Call this directly from a click handler: the window opens synchronously so
 * popup blockers allow it.
 */
export async function runMcpOAuthFlow({
  name,
  server,
}: {
  name: string;
  server: Record<string, unknown>;
}): Promise<McpOAuthFlowResult> {
  const popup = window.open("about:blank", "_blank");
  if (!popup) return { ok: false, reason: "popup_blocked" };
  // The provider's page must not be able to navigate this tab.
  popup.opener = null;

  try {
    const start = await McpService.startOAuth({
      name,
      server,
      timeout: OAUTH_TIMEOUT_SECONDS,
    });
    if (!start.ok || !start.job_id) {
      return { ok: false, reason: "failed", error: start.error ?? undefined };
    }
    const jobId = start.job_id;

    // The probe has to be waiting for the callback before the provider can
    // answer, so the window only leaves about:blank once it is.
    let status = await McpService.getOAuthStatus(jobId);
    for (
      let attempt = 0;
      attempt < CALLBACK_READY_ATTEMPTS &&
      status.status !== "succeeded" &&
      status.status !== "failed" &&
      !status.callback_ready;
      attempt += 1
    ) {
      // eslint-disable-next-line no-await-in-loop
      await sleep(CALLBACK_READY_POLL_MS);
      // eslint-disable-next-line no-await-in-loop
      status = await McpService.getOAuthStatus(jobId);
    }
    if (start.authorization_url) {
      popup.location.href = start.authorization_url;
    }

    for (
      let attempt = 0;
      attempt < OAUTH_TIMEOUT_SECONDS &&
      status.status !== "succeeded" &&
      status.status !== "failed";
      attempt += 1
    ) {
      // Not ending early on `popup.closed`: a provider's
      // Cross-Origin-Opener-Policy makes it read true during consent.
      // eslint-disable-next-line no-await-in-loop
      await sleep(STATUS_POLL_MS);
      // eslint-disable-next-line no-await-in-loop
      status = await McpService.getOAuthStatus(jobId);
    }

    if (status.status === "succeeded") return { ok: true, jobId };
    if (status.status === "failed") {
      return { ok: false, reason: "failed", error: status.error ?? undefined };
    }
    return { ok: false, reason: "not_completed" };
  } finally {
    popup.close();
  }
}
