"""Additional repositories cloned beside the main one at conversation start.

The main repository stays in ``selected_repository``/``selected_branch``/
``git_provider``; these repositories are cloned into their own folders next to
it, under the conversation's working folder.
"""

import os
import re
from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.git import (
    ensure_valid_git_branch_name,
    ensure_valid_repository_name,
)
from openhands.sdk.skills import PathTrigger, Skill

MULTI_REPOSITORY_CONVERSATIONS_ENV = 'ENABLE_MULTI_REPOSITORY_CONVERSATIONS'
MAX_ADDITIONAL_REPOSITORIES = 9
ADDITIONAL_REPOSITORIES_DISABLED = (
    'additional_repositories is not enabled on this server '
    f'({MULTI_REPOSITORY_CONVERSATIONS_ENV} is off)'
)

# Skill names the SDK gives instruction files (AGENTS.md, CLAUDE.md, ...).
_INSTRUCTION_FILE_SKILL_NAMES = frozenset(Skill.PATH_TO_THIRD_PARTY_SKILL_NAME.values())
_UNSAFE_FOLDER_CHARS = re.compile(r'[^A-Za-z0-9._-]+')


def multi_repository_conversations_enabled() -> bool:
    return os.getenv(MULTI_REPOSITORY_CONVERSATIONS_ENV, 'false').lower() in (
        'true',
        '1',
    )


class AdditionalRepositoryRequest(BaseModel):
    """An additional repository to clone when the conversation starts."""

    repository: str
    branch: str | None = None
    git_provider: ProviderType

    @field_validator('repository')
    @classmethod
    def _validate_repository(cls, repository: str) -> str:
        ensure_valid_repository_name(repository)
        return repository

    @field_validator('branch')
    @classmethod
    def _validate_branch(cls, branch: str | None) -> str | None:
        if branch is not None:
            ensure_valid_git_branch_name(branch)
        return branch


class AdditionalRepositoryStatus(Enum):
    CLONED = 'cloned'
    FAILED = 'failed'


class AdditionalRepository(BaseModel):
    """An additional repository of a conversation and the result of its clone."""

    repository: str
    branch: str | None = None
    git_provider: ProviderType | None = None
    folder: str = Field(
        description="Folder name under the conversation's working folder"
    )
    path: str = Field(description='Absolute path of the folder in the sandbox')
    status: AdditionalRepositoryStatus
    error: str | None = None


def _same_repository(
    repository: str,
    git_provider: ProviderType | None,
    other_repository: str,
    other_provider: ProviderType | None,
) -> bool:
    if repository.lower() != other_repository.lower():
        return False
    return (
        git_provider is None or other_provider is None or git_provider == other_provider
    )


def validate_additional_repositories(
    additional_repositories: Sequence[AdditionalRepositoryRequest],
    selected_repository: str | None,
    git_provider: ProviderType | None,
) -> None:
    """Raise ValueError for a list the start request cannot accept."""
    if not additional_repositories:
        return
    if not selected_repository:
        raise ValueError(
            'additional_repositories needs selected_repository as the main repository'
        )
    if len(additional_repositories) > MAX_ADDITIONAL_REPOSITORIES:
        raise ValueError(
            f'At most {MAX_ADDITIONAL_REPOSITORIES} additional repositories are allowed'
        )
    seen: list[tuple[str, ProviderType | None]] = [(selected_repository, git_provider)]
    for item in additional_repositories:
        if any(
            _same_repository(item.repository, item.git_provider, repo, provider)
            for repo, provider in seen
        ):
            raise ValueError(f'Repository {item.repository!r} is listed more than once')
        seen.append((item.repository, item.git_provider))


def _folder_candidate(name: str) -> str:
    return _UNSAFE_FOLDER_CHARS.sub('-', name).strip('-.') or 'repository'


def assign_repository_folders(
    selected_repository: str,
    additional_repositories: Sequence[AdditionalRepositoryRequest],
) -> list[str]:
    """Return a distinct folder name for each additional repository.

    The main repository keeps its folder (the last path segment). Each
    additional repository takes its last segment when free, otherwise its full
    path joined with dashes, otherwise that with a number appended.
    """
    used = {selected_repository.split('/')[-1].lower()}
    folders: list[str] = []
    for item in additional_repositories:
        segments = item.repository.split('/')
        short = _folder_candidate(segments[-1])
        full = _folder_candidate('-'.join(segments))
        folder = short if short.lower() not in used else full
        suffix = 2
        while folder.lower() in used:
            folder = f'{full}-{suffix}'
            suffix += 1
        used.add(folder.lower())
        folders.append(folder)
    return folders


def repositories_context(
    selected_repository: str,
    selected_branch: str | None,
    git_provider: ProviderType | None,
    main_path: str,
    additional_repositories: Sequence[AdditionalRepository],
) -> str:
    """Describe every repository of the conversation and where it is cloned."""

    def line(
        repository: str,
        path: str,
        branch: str | None,
        provider: ProviderType | None,
        status: str,
    ) -> str:
        parts = [f'`{repository}` at `{path}`']
        if branch:
            parts.append(f'branch `{branch}`')
        if provider:
            parts.append(f'provider {provider.value}')
        parts.append(status)
        return '- ' + ', '.join(parts)

    lines = [
        'This conversation works on several repositories, each in its own folder '
        'under the working directory. Run git commands inside the folder of the '
        'repository you are changing.',
        line(
            selected_repository,
            main_path,
            selected_branch,
            git_provider,
            'main repository',
        ),
    ]
    for repo in additional_repositories:
        if repo.status == AdditionalRepositoryStatus.CLONED:
            status = 'cloned'
        else:
            status = f'not cloned: {repo.error or "clone failed"}'
        lines.append(
            line(repo.repository, repo.path, repo.branch, repo.git_provider, status)
        )
    lines.append(
        'Instructions from AGENTS.md and similar files in an additional repository '
        'appear under its folder name, e.g. `agents:<folder>`.'
    )
    body = '\n'.join(lines)
    return f'<REPOSITORIES>\n{body}\n</REPOSITORIES>'


def _is_under(source: str | None, directory: str) -> bool:
    if not source:
        return False
    return source == directory or source.startswith(directory.rstrip('/') + '/')


def _relative_glob(pattern: str) -> str:
    while pattern.startswith('./'):
        pattern = pattern[2:]
    return pattern.lstrip('/')


def _scope_paths(skill: Skill, folder: str) -> Skill:
    """Prefix a path rule's globs with the repository folder.

    Rules match paths relative to the agent's working directory, which is the
    parent of every repository folder in a multi-repository conversation.
    """
    if not isinstance(skill.trigger, PathTrigger):
        return skill
    paths = [f'{folder}/{_relative_glob(p)}' for p in skill.trigger.paths]
    return skill.model_copy(update={'trigger': PathTrigger(paths=paths)})


def _scope_instruction_file_name(skill: Skill, folder: str) -> Skill:
    base, sep, rest = skill.name.partition(':')
    if base not in _INSTRUCTION_FILE_SKILL_NAMES:
        return skill
    name = f'{base}:{folder}/{rest}' if sep else f'{base}:{folder}'
    return skill.model_copy(update={'name': name})


def merge_repository_skills(
    skills: list[Skill],
    main_project_dir: str,
    main_folder: str,
    additional_skills: Sequence[tuple[AdditionalRepository, list[Skill]]],
) -> list[Skill]:
    """Merge each additional repository's project skills into ``skills``.

    ``skills`` is the main load (public, user, org and the main repository's
    project skills). Instruction files from additional repositories are renamed
    per repository so each one survives. Other project skills override public,
    user and org skills of the same name, as the main repository's do; the main
    repository and then earlier repositories win conflicts between projects.
    """
    merged: dict[str, Skill] = {}
    project_names: set[str] = set()
    for skill in skills:
        if _is_under(skill.source, main_project_dir):
            skill = _scope_paths(skill, main_folder)
            project_names.add(skill.name)
        merged[skill.name] = skill
    for repo, repo_skills in additional_skills:
        for skill in repo_skills:
            skill = _scope_paths(
                _scope_instruction_file_name(skill, repo.folder), repo.folder
            )
            if skill.name in project_names:
                continue
            project_names.add(skill.name)
            merged[skill.name] = skill
    return list(merged.values())
