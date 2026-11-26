"""Tests for sandbox_spec_service helpers.

Covers ``get_agent_server_image`` (derived from the installed
``openhands-agent-server`` package, with auto-correction of stale canonical
tags for self-hosted installs), ``is_custom_sandbox_spec`` (which compares
a sandbox spec id against the bundled default) and
``SandboxSpecService.resolve_existing_sandbox_spec`` (the spec of a sandbox that
already exists, with the default as the fallback once its image is no longer
advertised).
"""

import importlib.metadata
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import (
    AgentProfileSandboxSpec,
    SandboxSpecInfo,
)
from openhands.app_server.sandbox.sandbox_spec_service import (
    _bundled_agent_server_version,
    agent_profile_spec_lookup,
    get_agent_server_image,
    is_custom_sandbox_spec,
    resolve_sandbox_spec,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """All the @cache'd helpers in this module need their caches cleared around
    every test so we don't leak env-var or mocked-version state between cases.
    (_bundled_default_image isn't @cache'd; it just calls _bundled_agent_server_version.)"""
    get_agent_server_image.cache_clear()
    _bundled_agent_server_version.cache_clear()
    yield
    get_agent_server_image.cache_clear()
    _bundled_agent_server_version.cache_clear()


def test_get_agent_server_image_derived_from_package_version():
    """The URL must be built from the installed openhands-agent-server version,
    not a hand-maintained constant — that's the whole point of removing the
    drift-prone AGENT_SERVER_IMAGE string."""
    fake_version = '9.9.9'
    with patch.object(
        importlib.metadata,
        'version',
        return_value=fake_version,
    ):
        assert get_agent_server_image() == (
            f'ghcr.io/openhands/agent-server:{fake_version}-python'
        )


def test_get_agent_server_image_returns_consistent_value_within_process():
    """``@cache`` should make repeat calls return the same object without
    re-reading importlib.metadata or re-checking env vars."""
    with patch.object(
        importlib.metadata,
        'version',
        return_value='1.0.0',
    ) as version_mock:
        first = get_agent_server_image()
        # Call many times — version_mock should only be hit once thanks to @cache.
        for _ in range(5):
            assert get_agent_server_image() is first
        assert version_mock.call_count == 1


def test_get_agent_server_image_honours_custom_repository():
    """Self-hosted customers can pin to their own image repository via
    AGENT_SERVER_IMAGE_REPOSITORY; the result must be honoured verbatim,
    including a non-default tag."""
    fake_version = '1.0.0'
    with patch.object(importlib.metadata, 'version', return_value=fake_version):
        with patch.dict(
            'os.environ',
            {
                'AGENT_SERVER_IMAGE_REPOSITORY': 'example.com/agent-server',
                'AGENT_SERVER_IMAGE_TAG': '9.9.9-python',
            },
            clear=False,
        ):
            assert get_agent_server_image() == 'example.com/agent-server:9.9.9-python'


def test_get_agent_server_image_auto_corrects_stale_canonical_tag():
    """Regression test for the staging 500.

    A canonical-repo + stale ``-python`` tag (the exact staging failure mode)
    must be silently re-pinned to the SDK-matching tag, so self-hosted
    installs keep working when they ship a stale AGENT_SERVER_IMAGE_TAG.
    """
    fake_version = '9.9.9'
    with patch.object(importlib.metadata, 'version', return_value=fake_version):
        with patch.dict(
            'os.environ',
            {
                'AGENT_SERVER_IMAGE_REPOSITORY': 'ghcr.io/openhands/agent-server',
                'AGENT_SERVER_IMAGE_TAG': '1.31.1-python',
            },
            clear=False,
        ):
            assert get_agent_server_image() == (
                f'ghcr.io/openhands/agent-server:{fake_version}-python'
            )


def test_get_agent_server_image_passes_through_canonical_tag_without_suffix():
    """Tags without a ``-`` separator (e.g. ``1.32.0``, ``nightly``) must not
    be silently rewritten by the auto-correct heuristic — that would lose
    legitimate canonical tags. The post-hoc 500-hint will still flag any
    actual SDK mismatch downstream."""
    fake_version = '9.9.9'
    with patch.object(importlib.metadata, 'version', return_value=fake_version):
        with patch.dict(
            'os.environ',
            {
                'AGENT_SERVER_IMAGE_REPOSITORY': 'ghcr.io/openhands/agent-server',
                'AGENT_SERVER_IMAGE_TAG': 'nightly',
            },
            clear=False,
        ):
            assert get_agent_server_image() == (
                'ghcr.io/openhands/agent-server:nightly'
            )


def test_get_agent_server_image_passes_through_canonical_repo_with_matching_tag():
    """Canonical repo + already-matching tag must be a no-op (no warning, no
    rewrite) — the most common case for fresh installs."""
    fake_version = '1.2.3'
    with patch.object(importlib.metadata, 'version', return_value=fake_version):
        with patch.dict(
            'os.environ',
            {
                'AGENT_SERVER_IMAGE_REPOSITORY': 'ghcr.io/openhands/agent-server',
                'AGENT_SERVER_IMAGE_TAG': f'{fake_version}-python',
            },
            clear=False,
        ):
            assert get_agent_server_image() == (
                f'ghcr.io/openhands/agent-server:{fake_version}-python'
            )


def test_is_custom_sandbox_spec_false_for_bundled_default():
    """A spec id equal to the bundled default is, by definition, not custom."""
    with patch.object(importlib.metadata, 'version', return_value='1.0.0'):
        bundled = get_agent_server_image()
        assert is_custom_sandbox_spec(bundled) is False


def test_is_custom_sandbox_spec_true_for_runtime_api_image():
    """A spec id from runtime-api (any non-default image) must be flagged custom."""
    with patch.object(importlib.metadata, 'version', return_value='1.0.0'):
        assert is_custom_sandbox_spec('ghcr.io/some/custom:0.0.1') is True


def test_is_custom_sandbox_spec_true_for_self_hosted_custom_repo():
    """A self-hosted customer who set AGENT_SERVER_IMAGE_REPOSITORY to a
    custom repo still has a non-default image; the post-hoc 500-hint must
    fire for them when their image's SDK doesn't match."""
    fake_version = '1.0.0'
    with patch.object(importlib.metadata, 'version', return_value=fake_version):
        with patch.dict(
            'os.environ',
            {
                'AGENT_SERVER_IMAGE_REPOSITORY': 'example.com/agent-server',
                'AGENT_SERVER_IMAGE_TAG': '9.9.9-python',
            },
            clear=False,
        ):
            assert (
                is_custom_sandbox_spec('example.com/agent-server:9.9.9-python') is True
            )


def test_get_agent_server_image_propagates_package_not_found():
    """openhands-agent-server is a hard runtime dependency; a missing install
    must surface as PackageNotFoundError at first call, not silently degrade."""
    with patch.object(
        importlib.metadata,
        'version',
        side_effect=importlib.metadata.PackageNotFoundError('openhands-agent-server'),
    ):
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            get_agent_server_image()


async def test_resolve_existing_sandbox_spec_returns_the_sandbox_spec():
    """A sandbox whose image runtime-api still advertises keeps its own spec."""
    default = SandboxSpecInfo(
        id='ghcr.io/openhands/agent-server:1.57.0-python', command=None
    )
    spec = SandboxSpecInfo(id='ghcr.io/some/custom:0.0.1', command=None)
    spec_service = PresetSandboxSpecService(specs=[default, spec])

    with patch('openhands.app_server.sandbox.sandbox_spec_service._logger') as logger:
        resolved = await spec_service.resolve_existing_sandbox_spec(
            spec.id, 'sandbox-1'
        )

    assert resolved is spec
    logger.warning.assert_not_called()


async def test_resolve_existing_sandbox_spec_falls_back_to_the_default():
    """After an agent-server image bump the old image has no spec; the default
    stands in, and the warning names the image, the sandbox and the pick."""
    old_image = 'ghcr.io/openhands/agent-server:1.50.1-python'
    default = SandboxSpecInfo(
        id='ghcr.io/openhands/agent-server:1.57.0-python',
        command=None,
        working_dir='/workspace/project',
    )
    spec_service = PresetSandboxSpecService(specs=[default])

    with patch('openhands.app_server.sandbox.sandbox_spec_service._logger') as logger:
        resolved = await spec_service.resolve_existing_sandbox_spec(
            old_image, 'sandbox-1'
        )

    assert resolved is default
    logged = logger.warning.call_args.args
    assert old_image in logged
    assert 'sandbox-1' in logged
    assert default.id in logged


_DEFAULT = SandboxSpecInfo(id='image:default', command=None)
_PERSONAL = SandboxSpecInfo(id='image:personal', command=None)
_PROFILE = SandboxSpecInfo(id='image:profile', command=None)
_EXPLICIT = SandboxSpecInfo(id='image:explicit', command=None)
_SPECS = PresetSandboxSpecService(specs=[_DEFAULT, _PERSONAL, _PROFILE, _EXPLICIT])
_LOG = logging.getLogger(__name__)


@pytest.mark.parametrize(
    ('explicit', 'personal', 'profile', 'expected'),
    [
        ('image:explicit', 'image:personal', 'image:profile', _EXPLICIT),
        (None, 'image:personal', 'image:profile', _PERSONAL),
        (None, None, 'image:profile', _PROFILE),
        (None, None, None, _DEFAULT),
        # Unadvertised saved images fall through, one tier at a time.
        (None, 'image:retired', 'image:profile', _PROFILE),
        (None, None, 'image:retired', _DEFAULT),
        (None, 'image:retired', 'image:retired', _DEFAULT),
    ],
)
async def test_resolve_sandbox_spec_precedence(explicit, personal, profile, expected):
    resolved = await resolve_sandbox_spec(
        explicit, personal, _SPECS, _LOG, AsyncMock(return_value=profile)
    )

    assert resolved is expected


async def test_resolve_sandbox_spec_rejects_an_unknown_explicit_spec():
    """An explicit request keeps its hard error; no saved image rescues it."""
    with pytest.raises(ValueError, match='image:retired'):
        await resolve_sandbox_spec(
            'image:retired',
            'image:personal',
            _SPECS,
            _LOG,
            AsyncMock(return_value='image:profile'),
        )


async def test_resolve_sandbox_spec_skips_the_profile_lookup_when_not_needed():
    """The effective profile is resolved only when no explicit or valid
    personal image already chose the spec."""
    lookup = AsyncMock(return_value='image:profile')

    await resolve_sandbox_spec('image:explicit', None, _SPECS, _LOG, lookup)
    await resolve_sandbox_spec(None, 'image:personal', _SPECS, _LOG, lookup)

    lookup.assert_not_awaited()


def _user_context(effective_profile_spec_id):
    context = MagicMock()
    context.get_agent_profile_sandbox_spec_id = AsyncMock(
        return_value=effective_profile_spec_id
    )
    return context


async def test_profile_spec_lookup_uses_the_launch_snapshot_without_a_lookup():
    context = _user_context('image:org-default')

    looked_up = await agent_profile_spec_lookup(
        context, AgentProfileSandboxSpec(spec_id='image:profile')
    )()

    assert looked_up == 'image:profile'
    context.get_agent_profile_sandbox_spec_id.assert_not_awaited()


async def test_profile_spec_lookup_keeps_a_snapshot_without_an_image():
    """A launch whose profile names no image must not pick up the effective
    profile's (for example the organization default's) image."""
    context = _user_context('image:org-default')

    looked_up = await agent_profile_spec_lookup(
        context, AgentProfileSandboxSpec(spec_id=None)
    )()

    assert looked_up is None
    context.get_agent_profile_sandbox_spec_id.assert_not_awaited()


async def test_profile_spec_lookup_without_a_snapshot_reads_the_effective_profile():
    context = _user_context('image:org-default')

    assert await agent_profile_spec_lookup(context, None)() == 'image:org-default'
    assert await agent_profile_spec_lookup(None, None)() is None


def test_profile_sandbox_snapshot_is_immutable():
    snapshot = AgentProfileSandboxSpec(spec_id='image:profile')
    with pytest.raises(ValueError):
        snapshot.spec_id = 'image:other'


async def test_effective_profile_image_reads_the_resolved_launch_view():
    from openhands.app_server.user.user_context import (
        effective_agent_profile_sandbox_spec_id,
    )
    from openhands.app_server.user.user_models import UserInfo

    context = MagicMock()
    context.get_user_info = AsyncMock(
        return_value=UserInfo(active_agent_profile_sandbox_spec_id='image:profile')
    )

    assert await effective_agent_profile_sandbox_spec_id(context) == 'image:profile'
    context.get_user_info.assert_awaited_once_with(resolve_agent_profile=True)


async def test_unresolvable_profile_does_not_block_a_direct_sandbox_start():
    from fastapi import HTTPException

    from openhands.app_server.user.user_context import (
        effective_agent_profile_sandbox_spec_id,
    )

    context = MagicMock()
    context.get_user_info = AsyncMock(side_effect=HTTPException(404))

    assert await effective_agent_profile_sandbox_spec_id(context) is None
