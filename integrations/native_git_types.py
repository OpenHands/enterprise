"""Validated provider response shapes used by the SaaS Git adapters."""

from typing import NotRequired, TypedDict

from pydantic import ConfigDict, with_config


@with_config(ConfigDict(extra='allow', strict=True, hide_input_in_errors=True))
class GitLabNamespace(TypedDict, total=False):
    kind: str


@with_config(ConfigDict(extra='allow', strict=True, hide_input_in_errors=True))
class GitLabOwner(TypedDict, total=False):
    id: int | str


@with_config(ConfigDict(extra='allow', strict=True, hide_input_in_errors=True))
class GitLabAccess(TypedDict):
    access_level: int


@with_config(ConfigDict(extra='allow', strict=True, hide_input_in_errors=True))
class GitLabPermissions(TypedDict):
    project_access: GitLabAccess | None
    group_access: GitLabAccess | None


@with_config(ConfigDict(extra='allow', strict=True, hide_input_in_errors=True))
class GitLabResource(TypedDict, total=False):
    id: int | str
    name: str
    full_path: str
    path_with_namespace: str
    star_count: int | None
    visibility: str
    namespace: GitLabNamespace
    owner: GitLabOwner
    url: str
    access_level: int
    permissions: GitLabPermissions


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
