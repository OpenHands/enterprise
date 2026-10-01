import { describe, it, expect, vi, test } from "vitest";
import { constructPullRequestUrl, getStatusText } from "#/utils/utils";
import { AgentState } from "#/types/agent-state";
import { I18nKey } from "#/i18n/declaration";

// Mock translations
const t = (key: string) => {
  const translations: { [key: string]: string } = {
    COMMON$WAITING_FOR_SANDBOX: "Waiting for sandbox",
    COMMON$STOPPING: "Stopping",
    COMMON$STARTING: "Starting",
    COMMON$SERVER_STOPPED: "Server stopped",
    COMMON$RUNNING: "Running",
    CONVERSATION$READY: "Ready",
    CONVERSATION$ERROR_STARTING_CONVERSATION: "Error starting conversation",
  };
  return translations[key] || key;
};

describe("getStatusText", () => {
  it("returns STOPPING when pausing", () => {
    const result = getStatusText({
      isPausing: true,
      isTask: false,
      taskStatus: null,
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.RUNNING,
      t,
    });

    expect(result).toBe(t(I18nKey.COMMON$STOPPING));
  });

  it("formats task status when polling a task", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: true,
      taskStatus: "WAITING_FOR_SANDBOX",
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.RUNNING,
      t,
    });

    expect(result).toBe("Waiting for sandbox");
  });

  it("returns task detail when task status is ERROR and detail exists", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: true,
      taskStatus: "ERROR",
      taskDetail: "Sandbox failed",
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.RUNNING,
      t,
    });

    expect(result).toBe("Sandbox failed");
  });

  it("returns translated error when task status is ERROR and no detail", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: true,
      taskStatus: "ERROR",
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.RUNNING,
      t,
    });

    expect(result).toBe(t(I18nKey.CONVERSATION$ERROR_STARTING_CONVERSATION));
  });

  it("returns READY translation when task is ready", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: true,
      taskStatus: "READY",
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.RUNNING,
      t,
    });

    expect(result).toBe(t(I18nKey.CONVERSATION$READY));
  });

  it("returns STARTING when starting status is true", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: false,
      taskStatus: null,
      taskDetail: null,
      isStartingStatus: true,
      isStopStatus: false,
      curAgentState: AgentState.INIT,
      t,
    });

    expect(result).toBe(t(I18nKey.COMMON$STARTING));
  });

  it("returns SERVER_STOPPED when stop status is true", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: false,
      taskStatus: null,
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: true,
      curAgentState: AgentState.STOPPED,
      t,
    });

    expect(result).toBe(t(I18nKey.COMMON$SERVER_STOPPED));
  });

  it("returns errorMessage when agent state is ERROR", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: false,
      taskStatus: null,
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.ERROR,
      errorMessage: "Something broke",
      t,
    });

    expect(result).toBe("Something broke");
  });

  it("returns default RUNNING status", () => {
    const result = getStatusText({
      isPausing: false,
      isTask: false,
      taskStatus: null,
      taskDetail: null,
      isStartingStatus: false,
      isStopStatus: false,
      curAgentState: AgentState.RUNNING,
      t,
    });

    expect(result).toBe(t(I18nKey.COMMON$RUNNING));
  });
});

describe("constructPullRequestUrl", () => {
  test.each([
    [
      "github",
      "owner/repo",
      "github.com",
      "https://github.com/owner/repo/pull/42",
    ],
    [
      "gitlab",
      "group/sub/repo",
      "gitlab.acme.dev",
      "https://gitlab.acme.dev/group/sub/repo/-/merge_requests/42",
    ],
    [
      "bitbucket",
      "workspace/repo",
      "bitbucket.org",
      "https://bitbucket.org/workspace/repo/pull-requests/42",
    ],
    [
      "bitbucket_data_center",
      "PROJ/repo",
      "https://bb.acme.dev",
      "https://bb.acme.dev/projects/PROJ/repos/repo/pull-requests/42",
    ],
    [
      "azure_devops",
      "org/project/repo",
      "dev.azure.com",
      "https://dev.azure.com/org/project/_git/repo/pullrequest/42",
    ],
    [
      "forgejo",
      "owner/repo",
      "codeberg.org",
      "https://codeberg.org/owner/repo/pulls/42",
    ],
  ] as const)(
    "builds the %s pull request URL",
    (provider, repo, host, expected) => {
      expect(constructPullRequestUrl(provider, repo, 42, host)).toBe(expected);
    },
  );

  it("returns an empty string when the URL cannot be built", () => {
    expect(constructPullRequestUrl("github", "owner/repo", 42, null)).toBe("");
    expect(
      constructPullRequestUrl("azure_devops", "org/repo", 42, "dev.azure.com"),
    ).toBe("");
    expect(
      constructPullRequestUrl(
        "enterprise_sso",
        "owner/repo",
        42,
        "sso.acme.dev",
      ),
    ).toBe("");
  });
});
