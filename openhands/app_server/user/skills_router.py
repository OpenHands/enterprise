from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, cast
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from openhands.app_server.app_conversation.skill_loader import (
    _match_url_source_to_provider,
    _split_git_url_source,
    authenticate_marketplace_sources,
    explicit_source_provider,
    get_azure_devops_bearer_token,
    is_agent_server_repository_source,
    provider_handler_with_token,
)
from openhands.app_server.app_conversation.skill_loader import (
    parse_marketplace_source as _parse_marketplace_source,
)
from openhands.app_server.config import depends_user_context
from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderHandler,
    ProviderType,
)
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.marketplace import Marketplace
from openhands.sdk.plugin.format.claude_code import (
    PLUGIN_MANIFEST_DIRS,
    PLUGIN_MANIFEST_FILE,
)
from openhands.sdk.skills import (
    KeywordTrigger,
    Skill,
    TaskTrigger,
    load_public_skills,
)

router = APIRouter(prefix='/skills', tags=['Skills'], dependencies=get_dependencies())
user_context_dependency = depends_user_context()

USER_SKILLS_DIR = Path.home() / '.openhands' / 'microagents'

# Same rule as the SDK's cached clones: only a full SHA needs full history.
_FULL_SHA_RE = re.compile(r'^[0-9a-f]{40}$')

# Preview error for a repository a conversation would load nothing from.
_NOTHING_TO_LOAD = 'no marketplace manifest or skill folders found'

# How conversations read a repository: through its marketplace manifest, as a
# plain folder of skills, or as a plugin without a marketplace manifest, in
# which case only its skills load.
MarketplaceLayout = Literal['marketplace', 'skills', 'plugin']


class SkillInfo(BaseModel):
    """Information about a single available skill."""

    name: str
    type: str  # 'knowledge', 'repo', or 'task'
    source: str  # 'global' or 'user'
    triggers: list[str] | None = None
    description: str | None = None


class SkillPage(BaseModel):
    """Paginated response for the skills search endpoint."""

    items: list[SkillInfo]
    next_page_id: str | None = None


class MarketplacePluginPreview(BaseModel):
    """A plugin advertised by a marketplace manifest.

    The UI operates at the plugin level, so a plugin's bundled skills are not
    expanded here; only the plugin itself is surfaced.
    """

    name: str
    description: str | None = None
    source: str  # the marketplace registration source (e.g. 'github:owner/repo')
    marketplace: str  # the marketplace registration name this plugin belongs to


class SkillNameConflict(BaseModel):
    """A marketplace skill whose name collides with a built-in skill.

    Skill enablement is keyed by name, so disabling the built-in skill also
    disables the same-named marketplace skill.
    """

    name: str
    marketplace: str  # the marketplace registration name
    source: str  # the marketplace registration source (e.g. 'github:owner/repo')
    conflicts_with: str  # source of the built-in skill ('global')


class MarketplaceSkillsPreviewResponse(BaseModel):
    """Response for marketplace skills preview endpoint."""

    skills: list[SkillInfo]
    plugins: list[MarketplacePluginPreview]
    marketplace_skills: dict[str, list[str]]  # marketplace_name -> skill names
    errors: list[str]
    # Warnings only. Kept out of ``errors`` because clients treat any error as a
    # failed validation and refuse to save the marketplace.
    conflicts: list[SkillNameConflict] = []
    marketplace_layouts: dict[str, MarketplaceLayout] = {}


class MarketplaceCheckResponse(BaseModel):
    """Whether a marketplace will load in conversations, checked before saving."""

    status: Literal['ok', 'error', 'unchecked']
    # Why it failed or could not be checked; the client words the message.
    reason: (
        Literal[
            'not_found',
            'ref_not_found',
            'path_not_found',
            'empty',
            'unsupported',
            'sandbox_folder',
            'unknown_host',
            'failed',
        ]
        | None
    ) = None
    plugin_count: int = 0
    skill_count: int = 0
    # How a repository that passed will be read, so the client can explain it.
    layout: MarketplaceLayout | None = None


def _parse_skill_frontmatter(file_path: Path) -> dict | None:
    """Parse YAML frontmatter from a skill markdown file.

    Returns the frontmatter dict, or None if parsing fails.
    """
    try:
        text = file_path.read_text(encoding='utf-8')
    except Exception:
        return None

    if not text.startswith('---'):
        return None

    end = text.find('---', 3)
    if end == -1:
        return None

    try:
        return yaml.safe_load(text[3:end])
    except yaml.YAMLError as e:
        logger.warning(f'Invalid YAML frontmatter in {file_path}: {e}')
        return None


def _load_skills_from_dir(skills_dir: Path, source: str) -> list[SkillInfo]:
    """Load skill metadata from a directory of markdown files.

    Args:
        skills_dir: Path to the skills directory.
        source: Source label ('global' or 'user').

    Returns:
        List of SkillInfo objects parsed from the directory.
    """
    skills: list[SkillInfo] = []
    if not skills_dir.exists():
        return skills

    for md_file in skills_dir.rglob('*.md'):
        if md_file.name == 'README.md':
            continue

        try:
            fm = _parse_skill_frontmatter(md_file)
            if not isinstance(fm, dict):
                continue

            # Use name from frontmatter, falling back to filename stem
            name = fm.get('name') or md_file.stem

            # Determine type from frontmatter
            skill_type = fm.get('type', 'knowledge')
            triggers = fm.get('triggers') or None

            skills.append(
                SkillInfo(
                    name=name,
                    type=skill_type,
                    source=source,
                    triggers=triggers,
                )
            )
        except Exception as e:
            logger.warning(f'Failed to parse skill file {md_file}: {e}')

    return skills


def _public_skill_info(skill: Skill) -> SkillInfo:
    trigger = skill.trigger
    triggers: list[str] | None = None
    if isinstance(trigger, KeywordTrigger):
        triggers = trigger.keywords
    elif isinstance(trigger, TaskTrigger):
        triggers = trigger.triggers
    return SkillInfo(
        name=skill.name,
        type='task' if isinstance(trigger, TaskTrigger) else 'knowledge',
        source='global',
        triggers=triggers or None,
        description=skill.description,
    )


async def _load_global_skills() -> list[SkillInfo]:
    """The public skills conversations load, from the OpenHands/extensions repo.

    Uses the same loader, ref (``EXTENSIONS_REF``) and cached clone as the
    agent-server. The SDK caches the result in-process, so repeated calls do
    not re-read the clone. Returns no skills, rather than failing, when the
    repository can't be reached and no clone is cached.
    """
    try:
        skills = await asyncio.to_thread(load_public_skills)
    except Exception as e:
        logger.warning(f'Failed to load public skills: {e}')
        return []
    if not skills:
        logger.warning('No public skills loaded from the extensions repository')
    return [_public_skill_info(skill) for skill in skills]


def _manifest_entry_skill_names(
    marketplace_root: Path, source: object, is_plugin: bool
) -> set[str]:
    """Names of the skills a manifest entry ships from inside the marketplace repo.

    Mirrors the agent-server's discovery (a plugin's ``skills/`` directory, else
    a root ``SKILL.md``) but reads frontmatter only, so nothing from the repo is
    loaded. The manifest is untrusted: remote and absolute sources are skipped
    and every path must resolve inside the cloned marketplace.
    """
    if not isinstance(source, str) or '://' in source or source.startswith(('/', '~')):
        return set()

    names: set[str] = set()
    try:
        root = marketplace_root.resolve()
        entry_dir = (root / source).resolve()
        if not entry_dir.is_relative_to(root) or not entry_dir.is_dir():
            return names

        skills_dir = entry_dir / 'skills'
        if is_plugin and skills_dir.is_dir():
            candidates = sorted(skills_dir.iterdir())
        else:
            candidates = [entry_dir]

        for candidate in candidates:
            if candidate.is_dir():
                skill_md = next(
                    (
                        f
                        for f in sorted(candidate.iterdir())
                        if f.is_file() and f.name.lower() == 'skill.md'
                    ),
                    None,
                )
                fallback_name = candidate.name
            elif candidate.suffix.lower() == '.md' and candidate.name != 'README.md':
                skill_md, fallback_name = candidate, candidate.stem
            else:
                continue

            if skill_md is None or not skill_md.resolve().is_relative_to(root):
                continue

            fm = _parse_skill_frontmatter(skill_md)
            declared_name = fm.get('name') if isinstance(fm, dict) else None
            names.add(str(declared_name or fallback_name))
    except Exception as e:
        logger.warning(f'Failed to inspect marketplace entry {source!r}: {e}')

    return names


def _plain_repo_skills(repo_dir: Path, marketplace_name: str) -> list[SkillInfo]:
    """Skills a conversation loads from a repository without a manifest.

    Mirrors the agent-server's rule (``Marketplace.from_skills_dir`` in the SDK):
    the ``<name>/SKILL.md`` folders in ``skills/`` and ``.skills/``, or, when
    neither exists, those directly in ``repo_dir``, which is where a repository
    path pointing at the skills folder lands. Loose markdown files are not
    skills, and every path must resolve inside the clone.
    """
    root = repo_dir.resolve()
    search_dirs = [d for d in (root / 'skills', root / '.skills') if d.is_dir()]

    skills: dict[str, SkillInfo] = {}
    for search_dir in search_dirs or [root]:
        for candidate in sorted(search_dir.iterdir()):
            if not candidate.is_dir() or not candidate.resolve().is_relative_to(root):
                continue
            skill_md = next(
                (
                    f
                    for f in sorted(candidate.iterdir())
                    if f.is_file() and f.name.lower() == 'skill.md'
                ),
                None,
            )
            if skill_md is None or not skill_md.resolve().is_relative_to(root):
                continue
            try:
                # Conversations skip a skill the SDK can't load, such as one with
                # invalid frontmatter or an invalid .mcp.json, so the preview does too.
                name = Skill.load(skill_md, strict=False).name
            except Exception as e:
                logger.warning(f'Skipping skill {skill_md}: {e}')
                continue

            fm = _parse_skill_frontmatter(skill_md)
            fm = fm if isinstance(fm, dict) else {}
            triggers = fm.get('triggers')
            skills.setdefault(
                name,
                SkillInfo(
                    name=name,
                    type=str(fm.get('type', 'knowledge')),
                    source=f'marketplace:{marketplace_name}',
                    triggers=[str(t) for t in triggers]
                    if isinstance(triggers, list)
                    else None,
                ),
            )
    return list(skills.values())


def _is_plugin(repo_dir: Path) -> bool:
    """Whether a repository without a marketplace manifest is a single plugin.

    Conversations load only such a plugin's skills, not its hooks, MCP servers
    or commands.
    """
    return any(
        (repo_dir / manifest_dir / PLUGIN_MANIFEST_FILE).is_file()
        for manifest_dir in PLUGIN_MANIFEST_DIRS
    )


@router.get(
    '/search',
    response_model=SkillPage,
)
async def search_skills(
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title='The max number of results in the page',
            gt=0,
            le=100,
        ),
    ] = 100,
) -> SkillPage:
    """Search / list available global and user-level skills.

    Returns skill metadata so the frontend can render a toggle list.
    """
    skills = await _load_global_skills()

    # Load user-level skills
    try:
        skills.extend(_load_skills_from_dir(USER_SKILLS_DIR, 'user'))
    except Exception as e:
        logger.warning(f'Failed to load user skills: {e}')

    # Sort by source (global first), then by name
    skills.sort(key=lambda s: (s.source, s.name))

    # Apply cursor-based pagination
    start = 0
    if page_id is not None:
        for i, skill in enumerate(skills):
            if skill.name == page_id:
                start = i + 1
                break

    page = skills[start : start + limit]
    next_page_id = (
        page[-1].name if len(page) == limit and start + limit < len(skills) else None
    )

    return SkillPage(items=page, next_page_id=next_page_id)


def _bearer_auth_git_env(
    clone_url: str, bearer_token: str | None
) -> dict[str, str] | None:
    """Return a git environment sending ``bearer_token`` to the clone host.

    Uses GIT_CONFIG_* so the token stays out of argv, the URL and any
    persisted git config. ``None`` keeps the inherited environment.
    """
    if not bearer_token:
        return None
    parsed = urlparse(clone_url)
    origin = f'{parsed.scheme}://{parsed.hostname}'
    if parsed.port:
        origin += f':{parsed.port}'
    # Append after any GIT_CONFIG_* entries the server already inherits.
    n = int(os.environ.get('GIT_CONFIG_COUNT') or 0)
    return {
        **os.environ,
        'GIT_CONFIG_COUNT': str(n + 1),
        f'GIT_CONFIG_KEY_{n}': f'http.{origin}/.extraheader',
        f'GIT_CONFIG_VALUE_{n}': f'Authorization: Bearer {bearer_token}',
    }


async def _clone_marketplace_repo(
    marketplace: MarketplaceRegistration,
    user_context: UserContext,
) -> tuple[Path | None, str]:
    """Clone a marketplace repository to a temporary directory.

    Args:
        marketplace: MarketplaceRegistration with source, ref, and repo_path
        user_context: UserContext for accessing provider tokens

    Returns:
        Tuple of (cloned_path or None, error_message or '')
    """
    provider, repo_path = _parse_marketplace_source(marketplace.source)

    # Validate repo path format
    if not repo_path or '/' not in repo_path:
        return None, f'Invalid repository path: {repo_path}'

    # Authenticate URL/scp sources against the provider whose host matches
    # (pinned), so private self-hosted repos (e.g. Bitbucket DC) clone with
    # credentials. Non-URL owner/repo sources fall through to the public path.
    authenticated_url = None
    bearer_token = None
    try:
        matched = await _match_url_source_to_provider(marketplace.source, user_context)
        if matched is not None:
            matched_provider, matched_repo = matched
            handler = await user_context.get_provider_handler()
            authenticated_url = await handler.get_authenticated_git_url(
                matched_repo, specified_provider=matched_provider
            )
            # Entra tokens are not embedded in the URL; send them as a header.
            if matched_provider == ProviderType.AZURE_DEVOPS:
                bearer_token = await get_azure_devops_bearer_token(user_context)
        elif (
            named_provider := explicit_source_provider(marketplace.source)
        ) is not None:
            # github:owner/repo or a public provider URL: resolve only against
            # that provider; without its token, clone the public source.
            named_handler = await provider_handler_with_token(
                user_context, named_provider
            )
            if named_handler is not None:
                authenticated_url = await named_handler.get_authenticated_git_url(
                    repo_path, specified_provider=named_provider
                )
                if named_provider == ProviderType.AZURE_DEVOPS:
                    bearer_token = await get_azure_devops_bearer_token(user_context)
        elif '://' not in repo_path:
            # Bare owner/repo: resolve against every provider.
            provider_tokens = await user_context.get_provider_tokens()
            if provider_tokens:
                typed_provider_tokens = cast(PROVIDER_TOKEN_TYPE, provider_tokens)
                client = ProviderHandler(
                    provider_tokens=MappingProxyType(typed_provider_tokens),
                    external_auth_id=await user_context.get_user_id(),
                )
                authenticated_url = await client.get_authenticated_git_url(repo_path)
    except Exception as e:
        logger.warning(
            f'Failed to get authenticated URL for {repo_path}: {e}, '
            'will try unauthenticated clone'
        )

    if authenticated_url:
        clone_url = authenticated_url
    elif '://' in repo_path:
        # repo_path still carries a scheme, so the source is a full URL on a host
        # _parse_marketplace_source did not recognize AND no configured provider
        # matched it. Refuse rather than git-cloning an arbitrary (possibly
        # attacker-controlled) host (SSRF). Recognized public domains
        # (github.com/gitlab.com/bitbucket.org) reduce to owner/repo above and
        # fall through to the public clone below regardless of login provider.
        return None, f'Unsupported marketplace host: {marketplace.source}'
    else:
        # Public bare owner/repo, recognized provider prefix, or a recognized
        # public-domain URL: clone from the public domain, no auth needed.
        provider_domain_map = {
            'github': 'github.com',
            'gitlab': 'gitlab.com',
            'bitbucket': 'bitbucket.org',
        }
        clone_url = (
            f'https://{provider_domain_map.get(provider, "github.com")}/{repo_path}.git'
        )

    # Create unique temporary directory for this clone using tempfile.mkdtemp
    try:
        clone_dir = Path(
            tempfile.mkdtemp(prefix=f'openhands_marketplace_{marketplace.name}_')
        )

        # Run git without a shell (argv form) and use ``--`` so a source/ref
        # that begins with '-' can never be parsed as a git option (argument
        # injection). Reject leading-'-' values outright as defense in depth.
        if clone_url.startswith('-'):
            _cleanup_clone_dir(clone_dir)
            return None, f'Invalid clone URL: {clone_url}'

        ref = marketplace.ref
        if ref and ref.startswith('-'):
            _cleanup_clone_dir(clone_dir)
            return None, f'Invalid ref: {ref}'
        # Clone like the agent-server does: shallow for a branch or tag, full
        # history only when a commit SHA must be checked out.
        pinned_sha = ref if ref and _FULL_SHA_RE.fullmatch(ref) else None
        clone_args = ['git', 'clone']
        if not pinned_sha:
            clone_args += ['--depth', '1']
            if ref:
                clone_args += ['--branch', ref]
        result = subprocess.run(
            [*clone_args, '--', clone_url, str(clone_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            env=_bearer_auth_git_env(clone_url, bearer_token),
        )

        if result.returncode != 0:
            _cleanup_clone_dir(clone_dir)
            return None, f'Git clone failed: {result.stderr}'

        if pinned_sha:
            checkout_result = subprocess.run(
                ['git', '-C', str(clone_dir), 'checkout', pinned_sha],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if checkout_result.returncode != 0:
                _cleanup_clone_dir(clone_dir)
                return None, f'Git checkout failed: {checkout_result.stderr}'

        # Navigate to repo_path if specified
        if marketplace.repo_path:
            skills_path = clone_dir / marketplace.repo_path
            if not skills_path.exists():
                _cleanup_clone_dir(clone_dir)
                return None, f'Repo path not found: {marketplace.repo_path}'
            return skills_path, ''

        return clone_dir, ''

    except subprocess.TimeoutExpired:
        return None, 'Git clone timed out'
    except Exception as e:
        return None, f'Clone failed: {str(e)}'


def _cleanup_clone_dir(clone_dir: Path) -> None:
    """Clean up a cloned repository directory."""
    try:
        if clone_dir.exists():
            shutil.rmtree(clone_dir)
    except Exception as e:
        logger.debug(f'Failed to clean up clone directory {clone_dir}: {e}')


@router.post(
    '/marketplace-skills',
    response_model=MarketplaceSkillsPreviewResponse,
)
async def get_marketplace_skills(
    marketplaces: list[MarketplaceRegistration],
    user_context: UserContext = user_context_dependency,
) -> MarketplaceSkillsPreviewResponse:
    """Get skills from marketplace repositories.

    This endpoint fetches and returns skill metadata from marketplace repos
    without requiring an active sandbox session. Useful for previewing what
    skills a marketplace provides before or after adding it.

    Each call clones the marketplace repos fresh; this endpoint is admin-only
    and low-traffic, so caching the git clones here would buy little for the
    cross-user / pod-local complexity a shared cache introduces.

    Args:
        marketplaces: List of marketplace registrations to fetch skills from.

    Returns:
        MarketplaceSkillsPreviewResponse with skill metadata and any errors.
    """
    all_skills: list[SkillInfo] = []
    plugins: list[MarketplacePluginPreview] = []
    marketplace_skills: dict[str, list[str]] = {}
    errors: list[str] = []
    conflicts: list[SkillNameConflict] = []
    marketplace_layouts: dict[str, MarketplaceLayout] = {}

    # Built-in skill names, to flag marketplace skills that share one
    builtin_names = {skill.name for skill in await _load_global_skills()}

    # Track cloned directories for cleanup
    cloned_dirs: list[Path] = []

    try:
        for marketplace in marketplaces:
            # Clone the marketplace repo
            clone_path, error = await _clone_marketplace_repo(marketplace, user_context)

            if error:
                errors.append(f'{marketplace.name}: {error}')
                continue

            if clone_path is None:
                errors.append(f'{marketplace.name}: Failed to clone repository')
                continue

            cloned_dirs.append(clone_path)

            # Prefer the marketplace manifest so we operate at the *plugin* level.
            # ``Marketplace.load`` parses ``.plugin/marketplace.json`` (or
            # ``.claude-plugin/marketplace.json``) and exposes the plugins and any
            # standalone skills the marketplace advertises. A plugin's bundled
            # skills are intentionally not expanded — the UI shows plugins, not
            # their internals.
            skill_names: list[str] = []
            # Every skill name the marketplace ships, including skills bundled
            # in plugins, which are not surfaced as rows.
            shipped_names: set[str] = set()
            loaded_marketplace: Marketplace | None = None
            manifest_missing = False
            try:
                loaded_marketplace = Marketplace.load(clone_path)
            except FileNotFoundError:
                # No manifest: this is a plain skills repo, not a plugin
                # marketplace. Fall back to its skill folders below.
                manifest_missing = True
            except Exception as e:
                logger.warning(
                    f'Failed to parse marketplace manifest for {marketplace.name}: {e}'
                )
                errors.append(f'{marketplace.name}: invalid marketplace manifest')
                loaded_marketplace = None

            if loaded_marketplace is not None:
                marketplace_layouts[marketplace.name] = 'marketplace'
                for plugin_entry in loaded_marketplace.plugins:
                    plugins.append(
                        MarketplacePluginPreview(
                            name=plugin_entry.name,
                            description=plugin_entry.description,
                            source=marketplace.source,
                            marketplace=marketplace.name,
                        )
                    )
                    shipped_names |= _manifest_entry_skill_names(
                        clone_path, plugin_entry.source, is_plugin=True
                    )
                # Standalone skills declared in the manifest (not plugin-bundled).
                for skill_entry in loaded_marketplace.skills:
                    all_skills.append(
                        SkillInfo(
                            name=skill_entry.name,
                            type='knowledge',
                            source=f'marketplace:{marketplace.name}',
                            triggers=None,
                        )
                    )
                    skill_names.append(skill_entry.name)
                    shipped_names |= _manifest_entry_skill_names(
                        clone_path, skill_entry.source, is_plugin=False
                    )
            elif manifest_missing:
                # No manifest: list the skill folders a conversation loads.
                # Bundled plugin skills under plugins/*/skills/ are deliberately
                # not flattened — a plugin marketplace should ship a manifest.
                marketplace_layouts[marketplace.name] = (
                    'plugin' if _is_plugin(clone_path) else 'skills'
                )
                try:
                    plain_skills = _plain_repo_skills(clone_path, marketplace.name)
                except Exception as e:
                    logger.warning(f'Failed to read skills from {clone_path}: {e}')
                    plain_skills = []
                all_skills.extend(plain_skills)
                skill_names.extend(skill.name for skill in plain_skills)
                if not plain_skills:
                    errors.append(
                        f'{marketplace.name}: {_NOTHING_TO_LOAD} (expected '
                        '.plugin/marketplace.json or skills/<name>/SKILL.md)'
                    )

            marketplace_skills[marketplace.name] = skill_names

            shipped_names.update(skill_names)
            conflicts.extend(
                SkillNameConflict(
                    name=name,
                    marketplace=marketplace.name,
                    source=marketplace.source,
                    conflicts_with='global',
                )
                for name in sorted(shipped_names & builtin_names)
            )

    except Exception as e:
        logger.exception(
            'Unexpected error in marketplace-skills endpoint', stack_info=True
        )
        errors.append(f'Internal error: {str(e)}')
        # Clean up before raising
        for clone_dir in cloned_dirs:
            _cleanup_clone_dir(clone_dir)
        # Raise HTTP 500 for critical errors
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        # Clean up cloned directories
        for clone_dir in cloned_dirs:
            _cleanup_clone_dir(clone_dir)

    result = MarketplaceSkillsPreviewResponse(
        skills=all_skills,
        plugins=plugins,
        marketplace_skills=marketplace_skills,
        errors=errors,
        conflicts=conflicts,
        marketplace_layouts=marketplace_layouts,
    )

    return result


def _check_failure(error: str) -> MarketplaceCheckResponse:
    """Classify a preview error (``'<name>: <message>'``) for the client."""
    if _NOTHING_TO_LOAD in error:
        return MarketplaceCheckResponse(status='error', reason='empty')
    # A branch or tag is cloned shallow, so git clone itself reports it missing.
    if 'Git checkout failed' in error or 'not found in upstream' in error:
        return MarketplaceCheckResponse(status='error', reason='ref_not_found')
    if 'Repo path not found' in error:
        return MarketplaceCheckResponse(status='error', reason='path_not_found')
    if 'timed out' in error:
        return MarketplaceCheckResponse(status='unchecked', reason='failed')
    if any(
        marker in error
        for marker in (
            'Git clone failed',
            'Invalid repository path',
            'Invalid clone URL',
            # The host matched a connected provider, but resolving its
            # authenticated URL failed.
            'Unsupported marketplace host',
        )
    ):
        return MarketplaceCheckResponse(status='error', reason='not_found')
    return MarketplaceCheckResponse(status='unchecked', reason='failed')


@router.post('/marketplace-check', response_model=MarketplaceCheckResponse)
async def check_marketplace(
    marketplace: MarketplaceRegistration,
    user_context: UserContext = user_context_dependency,
) -> MarketplaceCheckResponse:
    """Check that a marketplace will load in conversations, before it is saved.

    The source is resolved as conversation start resolves it, so the check
    agrees with what the agent-server receives. Repository sources are then
    fetched with the preview. Sources the server does not fetch (sandbox
    folders, hosts that are not a connected provider) are reported unchecked.
    """
    # The resolved source may embed credentials: never log or return it.
    resolved = await authenticate_marketplace_sources(
        [marketplace.model_copy(update={'auto_load': True})], user_context
    )
    resolved_source = resolved[0].source if resolved else marketplace.source
    if not is_agent_server_repository_source(resolved_source):
        if '://' in resolved_source:
            return MarketplaceCheckResponse(status='error', reason='unsupported')
        return MarketplaceCheckResponse(status='unchecked', reason='sandbox_folder')

    split = _split_git_url_source(marketplace.source)
    if (
        split is not None
        and explicit_source_provider(marketplace.source) is None
        and await _match_url_source_to_provider(marketplace.source, user_context)
        is None
    ):
        return MarketplaceCheckResponse(status='unchecked', reason='unknown_host')

    try:
        preview = await get_marketplace_skills([marketplace], user_context=user_context)
    except HTTPException:
        return MarketplaceCheckResponse(status='unchecked', reason='failed')
    if preview.errors:
        return _check_failure(preview.errors[0])
    if not preview.plugins and not preview.skills:
        return MarketplaceCheckResponse(status='error', reason='empty')
    return MarketplaceCheckResponse(
        status='ok',
        plugin_count=len(preview.plugins),
        skill_count=len(preview.skills),
        layout=preview.marketplace_layouts.get(marketplace.name),
    )
