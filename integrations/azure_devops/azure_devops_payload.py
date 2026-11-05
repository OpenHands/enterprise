"""Read the Azure DevOps organization from a Service Hook payload.

Kept free of heavy imports so the automation event forwarder can use it as
well as the resolver view.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def extract_org_from_url(raw_url: str | None) -> str:
    if not raw_url:
        return ''

    parsed = urlparse(raw_url)
    hostname = parsed.hostname or ''
    path_parts = [part for part in parsed.path.split('/') if part]

    if hostname.endswith('dev.azure.com') and path_parts:
        return path_parts[0]

    if hostname.endswith('.visualstudio.com'):
        return hostname.split('.')[0]

    return ''


def extract_org(payload: dict[str, Any]) -> str:
    resource_containers = payload.get('resourceContainers') or {}
    for container_name in ('account', 'collection', 'project'):
        org = extract_org_from_url(
            (resource_containers.get(container_name) or {}).get('baseUrl')
        )
        if org:
            return org

    resource = payload.get('resource') or {}
    for candidate in (
        ((resource.get('pullRequest') or {}).get('repository') or {}).get('remoteUrl'),
        ((resource.get('pullRequest') or {}).get('repository') or {}).get('url'),
        ((resource.get('comment') or {}).get('_links') or {})
        .get('self', {})
        .get('href'),
        ((resource.get('_links') or {}).get('self') or {}).get('href'),
        resource.get('url'),
    ):
        org = extract_org_from_url(candidate)
        if org:
            return org

    return ''
