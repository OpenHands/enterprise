"""API routes for the Agent Canvas Apps approved for an organization (OHE-3773).

An instance Super Admin approves an app for an organization from a Git source.
The server fetches it once and stores the manifest and entrypoint of that exact
commit, so members only ever load the approved code. Every member can list the
organization's apps and switch each one on or off for themselves. Removing an
app also switches it off for every member.

The paths and payloads mirror the agent-server's ``/api/canvas-extensions`` so
Agent Canvas uses one contract for both backends. An app that declares a
managed backend is refused: there is no agent-server here to run it.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError

from openhands.agent_server.canvas_extensions.installed import (
    CanvasExtensionInstallationInterface,
)
from openhands.agent_server.canvas_extensions.manifest import (
    MANIFEST_FILENAME,
    CanvasExtensionManifest,
    resolve_entrypoint,
)
from openhands.agent_server.canvas_extensions_router import (
    InstallCanvasExtensionRequest,
    InstalledCanvasExtensionResponse,
    InstalledCanvasExtensionsListResponse,
    UninstallCanvasExtensionResponse,
    UpdateCanvasExtensionStateRequest,
    UpdateCanvasExtensionStateResponse,
)
from openhands.agent_server.utils import utc_now
from openhands.app_server.config import depends_user_context
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.user.skills_router import (
    _cleanup_clone_dir,
    _clone_marketplace_repo,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user_auth import reject_service_account
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import REJECT_X_ORG_ID_PATH_MISMATCH
from server.services.feature_flag_service import feature_flag_service
from storage.org_canvas_app import StoredOrgCanvasApp
from storage.org_canvas_app_store import (
    OrgCanvasAppAlreadyExistsError,
    OrgCanvasAppNotFoundError,
    OrgCanvasAppStore,
)
from storage.org_store import OrgStore

# The largest entrypoint an app may ship. Agent Canvas imports the whole file
# into the page, and it is stored in the database.
MAX_BUNDLE_BYTES = 5 * 1024 * 1024

user_context_dependency = depends_user_context()


async def validate_canvas_apps_enabled() -> None:
    """Reject every route unless a Super Admin turned Agent Canvas Apps on."""
    if not await feature_flag_service.resolve('ENABLE_CANVAS_APPS'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Agent Canvas Apps are turned off on this instance.',
        )


org_canvas_apps_router = APIRouter(
    prefix='/api/organizations',
    tags=['Org Canvas Apps'],
    dependencies=[
        REJECT_X_ORG_ID_PATH_MISMATCH,
        Depends(validate_canvas_apps_enabled),
    ],
)


@dataclass
class _FetchedApp:
    manifest: CanvasExtensionManifest
    bundle: bytes
    resolved_ref: str


def _carries_credentials(source: str) -> bool:
    """Whether an http(s) source embeds a username, which may be a token.

    Every member reads the stored source, so it must not hold one.
    """
    return source.startswith(('http://', 'https://')) and '@' in urlparse(source).netloc


def _read_package(clone_dir: Path, repo_path: str | None) -> _FetchedApp:
    """Validate the app package inside a clone and read what gets stored.

    Nothing outside the clone is read: the package directory, the manifest and
    the entrypoint must each resolve inside it, whatever symlinks it carries.
    """
    root = clone_dir.resolve()
    package_dir = (root / repo_path).resolve() if repo_path else root
    if not package_dir.is_relative_to(root) or not package_dir.is_dir():
        raise FileNotFoundError(repo_path)
    if not (package_dir / MANIFEST_FILENAME).resolve().is_relative_to(package_dir):
        raise ValueError('manifest resolves outside the app package')
    manifest = CanvasExtensionInstallationInterface.load_from_dir(package_dir)
    if manifest.backend is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Apps with a managed backend are not supported on this deployment.',
        )
    entrypoint = resolve_entrypoint(manifest, package_dir)
    if entrypoint.stat().st_size > MAX_BUNDLE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                'The app entrypoint is larger than '
                f'{MAX_BUNDLE_BYTES // (1024 * 1024)} MB.'
            ),
        )
    resolved_ref = subprocess.run(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    return _FetchedApp(
        manifest=manifest, bundle=entrypoint.read_bytes(), resolved_ref=resolved_ref
    )


def _to_response(
    app: StoredOrgCanvasApp, enabled: bool
) -> InstalledCanvasExtensionResponse:
    """The app in the agent-server's shape, with ``enabled`` as the caller has it."""
    try:
        manifest = CanvasExtensionManifest.model_validate(app.manifest)
    except ValidationError:
        # A manifest stored under an earlier SDK release may no longer validate;
        # keep the rest of the organization's list readable.
        manifest = None
    return InstalledCanvasExtensionResponse(
        name=app.name,
        version=manifest.version if manifest else '',
        description=(manifest.description if manifest else None) or '',
        enabled=enabled,
        source=app.source,
        requested_ref=app.requested_ref,
        resolved_ref=app.resolved_ref,
        repo_path=app.repo_path,
        installed_at=app.installed_at.isoformat(),
        # The app lives in the database, not on a filesystem path.
        install_path='',
        manifest=manifest,
    )


@org_canvas_apps_router.get(
    '/{org_id}/canvas-extensions/installed',
    response_model=InstalledCanvasExtensionsListResponse,
)
async def list_org_canvas_apps(
    org_id: UUID,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> InstalledCanvasExtensionsListResponse:
    """List the organization's approved apps. Available to all org members.

    ``enabled`` is the caller's own setting for each app.
    """
    apps = await OrgCanvasAppStore(org_id).list_for(UUID(user_id))
    return InstalledCanvasExtensionsListResponse(
        canvas_extensions=[_to_response(app, enabled) for app, enabled in apps]
    )


@org_canvas_apps_router.post(
    '/{org_id}/canvas-extensions/install',
    response_model=InstalledCanvasExtensionResponse,
)
async def install_org_canvas_app(
    org_id: UUID,
    body: InstallCanvasExtensionRequest,
    user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
    user_context: UserContext = user_context_dependency,
) -> InstalledCanvasExtensionResponse:
    """Approve an app for the organization. Super Admin only.

    The source is fetched with the caller's Git provider connection and the
    commit it resolves to is what members load. The app starts switched off
    for everyone. 409 when the organization already has an app of that name:
    remove it first to approve another revision.
    """
    if await OrgStore.get_org_by_id(org_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Organization with id "{org_id}" not found',
        )
    try:
        registration = MarketplaceRegistration(
            name='canvas-app',
            source=body.source,
            ref=body.ref,
            repo_path=body.repo_path,
        )
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Invalid source. Use 'github:owner/repo' or a Git URL, with an "
                'optional ref and a path inside the repository.'
            ),
        ) from None
    if _carries_credentials(registration.source):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                'The source must not contain credentials. Connect the Git '
                'provider to read a private repository.'
            ),
        )

    # Cloned without ``repo_path`` so the clone root comes back: the commit is
    # read from it, the path is resolved inside it and all of it is removed.
    clone_dir, _ = await _clone_marketplace_repo(
        registration.model_copy(update={'repo_path': None}), user_context
    )
    if clone_dir is None:
        # The clone error can quote the authenticated URL, so it is not returned.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                'Could not read canvas extension source. Check the source and '
                'ref, and that your connected Git provider can read the repository.'
            ),
        )
    try:
        fetched = await asyncio.to_thread(
            _read_package, clone_dir, registration.repo_path
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f'No {MANIFEST_FILENAME} found at the resolved location. Source '
                "plus Path must point at the app's own directory, not the "
                'repository root.'
            ),
        ) from None
    except (ValidationError, ValueError, OSError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Invalid canvas extension. Ensure the manifest is well-formed.',
        ) from None
    finally:
        _cleanup_clone_dir(clone_dir)

    app = StoredOrgCanvasApp(
        org_id=org_id,
        name=fetched.manifest.name,
        source=registration.source,
        requested_ref=registration.ref,
        resolved_ref=fetched.resolved_ref,
        repo_path=registration.repo_path,
        manifest=fetched.manifest.model_dump(mode='json'),
        bundle=fetched.bundle,
        installed_at=utc_now(),
    )
    try:
        await OrgCanvasAppStore(org_id).create(app)
    except OrgCanvasAppAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Canvas extension '{app.name}' is already installed. Uninstall "
                'it first to install another revision.'
            ),
        ) from None
    logger.info(
        'org_canvas_apps:installed',
        extra={
            'caller_user_id': user_id,
            'org_id': str(org_id),
            'app_name': app.name,
            'resolved_ref': app.resolved_ref,
        },
    )
    return _to_response(app, enabled=False)


@org_canvas_apps_router.patch(
    '/{org_id}/canvas-extensions/installed/{name}',
    response_model=UpdateCanvasExtensionStateResponse,
    dependencies=[Depends(reject_service_account)],
)
async def set_org_canvas_app_enabled(
    org_id: UUID,
    name: str,
    body: UpdateCanvasExtensionStateRequest,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> UpdateCanvasExtensionStateResponse:
    """Switch an approved app on or off for the caller only. Any org member."""
    try:
        await OrgCanvasAppStore(org_id).set_enabled(UUID(user_id), name, body.enabled)
    except OrgCanvasAppNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Canvas extension '{name}' is not installed",
        ) from None
    return UpdateCanvasExtensionStateResponse(name=name, enabled=body.enabled)


@org_canvas_apps_router.delete(
    '/{org_id}/canvas-extensions/installed/{name}',
    response_model=UninstallCanvasExtensionResponse,
)
async def uninstall_org_canvas_app(
    org_id: UUID,
    name: str,
    user_id: str = Depends(require_permission(Permission.MANAGE_SUPER_ADMINS)),
) -> UninstallCanvasExtensionResponse:
    """Remove an app from the organization. Super Admin only.

    This also switches it off for every member who had it on.
    """
    if not await OrgCanvasAppStore(org_id).delete(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Canvas extension '{name}' is not installed",
        )
    logger.info(
        'org_canvas_apps:uninstalled',
        extra={'caller_user_id': user_id, 'org_id': str(org_id), 'app_name': name},
    )
    return UninstallCanvasExtensionResponse(
        message=f"Canvas extension '{name}' uninstalled"
    )


@org_canvas_apps_router.get('/{org_id}/canvas-extensions/installed/{name}/bundle')
async def get_org_canvas_app_bundle(
    org_id: UUID,
    name: str,
    user_id: str = Depends(require_permission(Permission.VIEW_ORG_SETTINGS)),
) -> Response:
    """Serve an approved app's stored entrypoint. Available to all org members."""
    bundle = await OrgCanvasAppStore(org_id).get_bundle(name)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Canvas extension '{name}' bundle not found",
        )
    return Response(content=bundle, media_type='text/javascript')
