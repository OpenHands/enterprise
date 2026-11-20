import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import McpService from "#/api/mcp-service/mcp-service.api";
import { runMcpOAuthFlow } from "#/utils/mcp-oauth-flow";

const REQUEST = { name: "issue-tracker", server: { url: "https://x.example" } };
const AUTHORIZATION_URL = "https://auth.example/authorize?state=abc";

function fakePopup() {
  return {
    opener: {} as unknown,
    closed: false,
    close: vi.fn(),
    location: { href: "about:blank" },
  };
}

function status(
  value: "pending" | "authorizing" | "succeeded" | "failed",
  extra: Record<string, unknown> = {},
) {
  return { ok: value !== "failed", status: value, job_id: "job-1", ...extra };
}

describe("runMcpOAuthFlow", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("opens the provider once the probe waits for it, and resolves with the job", async () => {
    const popup = fakePopup();
    const open = vi.spyOn(window, "open").mockReturnValue(popup as never);
    const start = vi.spyOn(McpService, "startOAuth").mockResolvedValue({
      ok: true,
      job_id: "job-1",
      authorization_url: AUTHORIZATION_URL,
    });
    const hrefAtReady: string[] = [];
    vi.spyOn(McpService, "getOAuthStatus")
      .mockResolvedValueOnce(status("pending"))
      .mockImplementationOnce(async () =>
        status("authorizing", { callback_ready: true }),
      )
      .mockImplementationOnce(async () => {
        hrefAtReady.push(popup.location.href);
        return status("succeeded");
      });

    const result = await runMcpOAuthFlow(REQUEST);

    // Synchronously, or a popup blocker refuses it.
    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(popup.opener).toBeNull();
    expect(start).toHaveBeenCalledWith({ ...REQUEST, timeout: 120 });
    expect(hrefAtReady).toEqual([AUTHORIZATION_URL]);
    expect(popup.close).toHaveBeenCalled();
    expect(result).toEqual({ ok: true, jobId: "job-1" });
  });

  it("reports a blocked window without starting a flow", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const start = vi.spyOn(McpService, "startOAuth");

    const result = await runMcpOAuthFlow(REQUEST);

    expect(result).toEqual({ ok: false, reason: "popup_blocked" });
    expect(start).not.toHaveBeenCalled();
  });

  it("reports the backend's message when the flow cannot start", async () => {
    const popup = fakePopup();
    vi.spyOn(window, "open").mockReturnValue(popup as never);
    vi.spyOn(McpService, "startOAuth").mockResolvedValue({
      ok: false,
      job_id: "job-1",
      error: "No OAuth metadata",
      error_kind: "connection",
    });

    const result = await runMcpOAuthFlow(REQUEST);

    expect(result).toEqual({
      ok: false,
      reason: "failed",
      error: "No OAuth metadata",
    });
    expect(popup.close).toHaveBeenCalled();
  });

  it("reports the backend's message when the provider refuses", async () => {
    vi.spyOn(window, "open").mockReturnValue(fakePopup() as never);
    vi.spyOn(McpService, "startOAuth").mockResolvedValue({
      ok: true,
      job_id: "job-1",
      authorization_url: AUTHORIZATION_URL,
    });
    vi.spyOn(McpService, "getOAuthStatus").mockResolvedValue(
      status("failed", { error: "access_denied" }),
    );

    expect(await runMcpOAuthFlow(REQUEST)).toEqual({
      ok: false,
      reason: "failed",
      error: "access_denied",
    });
  });

  it("keeps waiting while the window reads as closed, then gives up", async () => {
    // A provider's Cross-Origin-Opener-Policy makes `closed` read true during
    // consent, so it cannot mean the member walked away.
    const popup = fakePopup();
    popup.closed = true;
    vi.spyOn(window, "open").mockReturnValue(popup as never);
    vi.spyOn(McpService, "startOAuth").mockResolvedValue({
      ok: true,
      job_id: "job-1",
      authorization_url: AUTHORIZATION_URL,
    });
    const getStatus = vi
      .spyOn(McpService, "getOAuthStatus")
      .mockResolvedValue(status("authorizing", { callback_ready: true }));

    const pending = runMcpOAuthFlow(REQUEST);
    await vi.advanceTimersByTimeAsync(121_000);

    expect(await pending).toEqual({ ok: false, reason: "not_completed" });
    expect(getStatus.mock.calls.length).toBeGreaterThan(100);
  });
});
