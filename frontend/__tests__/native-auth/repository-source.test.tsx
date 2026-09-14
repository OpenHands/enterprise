import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useGitRepositories } from "#/hooks/query/use-git-repositories";
import { GitConnectionService } from "#/api/git-connection-service/git-connection-service.api";
import GitService from "#/api/git-service/git-service.api";

const mode = vi.hoisted(() => ({ native: true }));
vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({
    data: {
      app_mode: "saas",
      auth_mode: mode.native ? "native" : "keycloak",
      providers_configured: [],
    },
  }),
}));
vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: true }),
}));
// Mirrors the native V1 settings projection, independently of login brokers.
vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({
    data: { provider_tokens_set: { github: "github.com" } },
    isLoading: false,
  }),
}));

const wrapper = ({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);

describe("native GitHub repository discovery", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mode.native = true;
  });
  it("lists PAT repositories without requesting GitHub App installations", async () => {
    vi.spyOn(GitConnectionService, "list").mockResolvedValue({
      connections: [
        {
          provider: "github",
          host: "github.com",
          auth_type: "pat",
          status: "connected",
          account: {
            id: "1",
            login: "coder",
            display_name: null,
            avatar_url: null,
          },
          last_error: null,
        },
      ],
      capabilities: {
        github: {
          methods: ["pat"],
          hosts: ["github.com"],
          default_host: "github.com",
          installation_available: false,
        },
      },
    });
    const installations = vi.spyOn(GitService, "getUserInstallations");
    const list = vi
      .spyOn(GitService, "retrieveUserGitRepositories")
      .mockResolvedValue({
        items: [
          {
            id: "1",
            full_name: "coder/repo",
            git_provider: "github",
            is_public: false,
          },
        ],
        next_page_id: null,
      });
    const { result } = renderHook(
      () => useGitRepositories({ provider: "github" }),
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.data?.pages[0].items[0].full_name).toBe(
        "coder/repo",
      ),
    );
    expect(list).toHaveBeenCalledWith("github", undefined, 30);
    expect(installations).not.toHaveBeenCalled();
  });

  it("preserves App repository discovery for a configured native OAuth connection", async () => {
    vi.spyOn(GitConnectionService, "list").mockResolvedValue({
      connections: [
        {
          provider: "github",
          host: "github.com",
          auth_type: "oauth",
          status: "connected",
          account: {
            id: "1",
            login: "coder",
            display_name: null,
            avatar_url: null,
          },
          last_error: null,
        },
      ],
      capabilities: {
        github: {
          methods: ["pat", "oauth"],
          hosts: ["github.com"],
          default_host: "github.com",
          installation_available: true,
        },
      },
    });
    vi.spyOn(GitService, "getUserInstallations").mockResolvedValue({
      items: ["installation"],
      next_page_id: null,
    });
    const list = vi
      .spyOn(GitService, "retrieveInstallationRepositories")
      .mockResolvedValue({ items: [], next_page_id: null });
    const direct = vi.spyOn(GitService, "retrieveUserGitRepositories");
    renderHook(() => useGitRepositories({ provider: "github" }), { wrapper });
    await waitFor(() =>
      expect(list).toHaveBeenCalledWith(
        "github",
        0,
        ["installation"],
        undefined,
        30,
      ),
    );
    expect(direct).not.toHaveBeenCalled();
  });

  it("keeps legacy SaaS GitHub installation behavior without consulting native APIs", async () => {
    mode.native = false;
    const native = vi.spyOn(GitConnectionService, "list");
    vi.spyOn(GitService, "getUserInstallations").mockResolvedValue({
      items: ["legacy-installation"],
      next_page_id: null,
    });
    const list = vi
      .spyOn(GitService, "retrieveInstallationRepositories")
      .mockResolvedValue({ items: [], next_page_id: null });
    renderHook(() => useGitRepositories({ provider: "github" }), { wrapper });
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(native).not.toHaveBeenCalled();
  });
});
