"""Validated provider response shapes used by the SaaS Git adapters."""

from typing import NotRequired, TypedDict

from pydantic import ConfigDict, with_config


@with_config(ConfigDict(extra='allow', strict=True, hide_input_in_errors=True))
class GitHubNode(TypedDict):
    node_id: NotRequired[str]


class GitHubRepositoryPermissions(TypedDict, total=False):
    push: bool
    maintain: bool
    admin: bool


class GitHubRepositoryAccess(TypedDict, total=False):
    permissions: GitHubRepositoryPermissions | None


class GitCommitView(TypedDict):
    sha: str
    authors: str | None
    committed_date: str | None


class IssueCommentView(TypedDict):
    id: int
    body: str
    created_at: str
    user: str
