"""Which GitLab hooks are ours, and which install created each one.

Every hook the installer creates points at ``GITLAB_WEBHOOK_URL?oh_hook=<uuid>``,
where the uuid is that install's intent. GitLab doesn't return a hook's secret
token, so the uuid in its URL is the only way to tell our hooks apart. A hook at
the plain URL predates this scheme (a legacy hook).
"""

from urllib.parse import parse_qs, urlencode, urlsplit

from integrations.utils import GITLAB_WEBHOOK_URL

IDENTITY_PARAM = 'oh_hook'


def hook_url(hook_uuid: str) -> str:
    return f'{GITLAB_WEBHOOK_URL}?{urlencode({IDENTITY_PARAM: hook_uuid})}'


def points_at(url: str | None, base_url: str) -> bool:
    """True for ``base_url`` itself and for any identity URL built from it."""
    return url is not None and url.split('?', 1)[0] == base_url


def hook_identity(url: str) -> str | None:
    """The intent uuid in an identity URL, or None for a legacy hook."""
    values = parse_qs(urlsplit(url).query).get(IDENTITY_PARAM)
    return values[0] if values else None
