"""Authenticated release/version info for the Settings "About" UI.

Deliberately separate from ``/server_info`` (``status_router.py``): that
endpoint is an unauthenticated operational diagnostics route (uptime,
resources, io — see ``system_stats.get_system_stats``, which calls
``psutil``/``time.sleep``) and must keep its existing contract. This router
reuses the version-provider functions from ``system_stats`` directly rather
than importing the diagnostics route, and never touches process/host
resource stats.
"""

from fastapi import HTTPException, status
from fastapi.routing import APIRouter
from pydantic import BaseModel

from openhands.app_server.config import depends_user_context
from openhands.app_server.status.system_stats import (
    get_enterprise_release,
    get_sdk_version,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.version import get_version

# We use get_dependencies() here (same as user_router.py) to signal to the
# OpenAPI docs that this endpoint is protected. The actual protection is
# provided by SetAuthCookieMiddleware.
router = APIRouter(prefix='/release', tags=['Release'], dependencies=get_dependencies())
user_dependency = depends_user_context()


class ReleaseInfo(BaseModel):
    """Allowlisted release/version fields only.

    Deliberately excludes host/network details, usernames, org ids, and
    credentials — this must not grow to carry anything beyond version
    identifiers.
    """

    app_version: str
    sdk_version: str
    enterprise_release: str | None = None


@router.get('')
async def get_release_info(
    user_context: UserContext = user_dependency,
) -> ReleaseInfo:
    """Get the server's release/version info.

    Requires an authenticated session (unlike ``/server_info``, which is an
    unauthenticated diagnostics endpoint).
    """
    user = await user_context.get_user_info()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')
    return ReleaseInfo(
        app_version=get_version(),
        sdk_version=get_sdk_version(),
        enterprise_release=get_enterprise_release(),
    )
