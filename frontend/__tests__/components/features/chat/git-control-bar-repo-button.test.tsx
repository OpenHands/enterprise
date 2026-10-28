import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GitControlBarRepoButton } from "#/components/features/chat/git-control-bar-repo-button";

// Mock react-i18next
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

// Mock GitProviderIcon
vi.mock("#/components/shared/git-provider-icon", () => ({
  GitProviderIcon: ({ gitProvider }: { gitProvider: string }) => (
    <span data-testid="git-provider-icon">{gitProvider}</span>
  ),
}));

// Mock GitExternalLinkIcon
vi.mock(
  "#/components/features/chat/git-external-link-icon",
  () => ({
    GitExternalLinkIcon: () => (
      <span data-testid="git-external-link-icon">external</span>
    ),
  }),
);

// Mock RepoForkedIcon
vi.mock("#/icons/repo-forked.svg?react", () => ({
  default: () => <span data-testid="repo-forked-icon">forked</span>,
}));

vi.mock("#/hooks/use-provider-host", () => ({
  useProviderHost: () => null,
}));

// Mock constructRepositoryUrl
vi.mock("#/utils/utils", async (importOriginal) => {
  const actual = await importOriginal<typeof import("#/utils/utils")>();
  return {
    ...actual,
    constructRepositoryUrl: (provider: string, repo: string) =>
      `https://${provider}.com/${repo}`,
  };
});

describe("GitControlBarRepoButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("when repository is connected", () => {
    it("should render as a link with repository name", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository="owner/repo"
          gitProvider="github"
        />,
      );

      const link = screen.getByRole("link");
      expect(link).toHaveAttribute("href", "https://github.com/owner/repo");
      expect(link).toHaveAttribute("target", "_blank");
      expect(screen.getByText("owner/repo")).toBeInTheDocument();
    });

    it("should show git provider icon and external link icon", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository="owner/repo"
          gitProvider="github"
        />,
      );

      expect(screen.getByTestId("git-provider-icon")).toBeInTheDocument();
      expect(screen.getByTestId("git-external-link-icon")).toBeInTheDocument();
    });

    it("should not show repo forked icon", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository="owner/repo"
          gitProvider="github"
        />,
      );

      expect(
        screen.queryByTestId("repo-forked-icon"),
      ).not.toBeInTheDocument();
    });
  });

  describe("when no repository is connected", () => {
    it("should render 'No Repo Connected' text", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository={null}
          gitProvider={null}
        />,
      );

      expect(screen.getByTestId("git-control-bar-no-repo")).toBeInTheDocument();
      expect(
        screen.getByText("COMMON$NO_REPO_CONNECTED"),
      ).toBeInTheDocument();
    });

    it("should show repo forked icon instead of provider icon", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository={null}
          gitProvider={null}
        />,
      );

      expect(screen.getByTestId("repo-forked-icon")).toBeInTheDocument();
      expect(
        screen.queryByTestId("git-provider-icon"),
      ).not.toBeInTheDocument();
    });

    it("should not show external link icon", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository={null}
          gitProvider={null}
        />,
      );

      expect(
        screen.queryByTestId("git-external-link-icon"),
      ).not.toBeInTheDocument();
    });

    it("should not render an interactive control", () => {
      render(
        <GitControlBarRepoButton
          selectedRepository={null}
          gitProvider={null}
        />,
      );

      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
    });
  });
});
