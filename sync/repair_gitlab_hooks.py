"""Ask the GitLab webhook installer to replace a resource's hooks.

Use for a project or group with more than one OpenHands hook, or whose hook
deliveries are rejected with 403:

    python -m sync.repair_gitlab_hooks --project <id>
    python -m sync.repair_gitlab_hooks --group <id>

This makes one database write and no GitLab call: it records a reinstall
request on the resource's row. The installer's next run (every minute) deletes
every hook with our URL on the resource and creates one with the row's
credentials. A run already working on the row finishes first; the request
survives it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from integrations.types import GitLabResourceType
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.gitlab_webhook_store import GitlabWebhookStore


async def request_repair(resource_type: GitLabResourceType, resource_id: str) -> bool:
    webhook_store = await GitlabWebhookStore.get_instance()
    requested = await webhook_store.request_reinstall(resource_type, resource_id)
    logger.info(
        'gitlab_webhook.repair_requested',
        extra={
            'resource_type': resource_type.value,
            'resource_id': resource_id,
            'found': requested,
        },
    )
    return requested


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--project', help='GitLab project id')
    target.add_argument('--group', help='GitLab group id')
    args = parser.parse_args(argv)

    if args.project:
        resource_type, resource_id = GitLabResourceType.PROJECT, args.project
    else:
        resource_type, resource_id = GitLabResourceType.GROUP, args.group

    if not asyncio.run(request_repair(resource_type, resource_id)):
        print(
            f'No webhook row for {resource_type.value} {resource_id}', file=sys.stderr
        )
        return 1
    print(
        f'Reinstall requested for {resource_type.value} {resource_id}; '
        'the installer applies it on its next run'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
