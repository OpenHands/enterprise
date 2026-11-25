#!/usr/bin/env python
"""Build the OpenSandbox template that OpenHands sandboxes restore from.

The template is a golden snapshot of the published agent-server image with the
agent server already running in deferred-init mode. Each sandbox restores that
snapshot, and the app server sends its config on ``POST /api/init``.

Usage:
    export OPENSANDBOX_DOMAIN=...      # lifecycle server, e.g. localhost:18080
    export OPENSANDBOX_API_KEY=...
    python scripts/opensandbox/build_template.py --publish s3://bucket/publish

It prints the template id and the init API key. Configure the app server with
OPENSANDBOX_TEMPLATE_ID and OPENSANDBOX_INIT_API_KEY.
"""

import argparse
import asyncio
import os
import secrets
import sys
import time
from importlib.metadata import version

from opensandbox import SandboxManager
from opensandbox.config import ConnectionConfig
from opensandbox.models.templates import (
    CreateTemplateRequest,
    TemplatePhase,
    TemplateReadiness,
)

DEFAULT_IMAGE_REPOSITORY = 'ghcr.io/openhands/agent-server'
AGENT_SERVER_PORT = 8000


def default_image() -> str:
    """The published agent-server image matching the installed SDK."""
    return f'{DEFAULT_IMAGE_REPOSITORY}:{version("openhands-agent-server")}-python'


def template_request(
    args: argparse.Namespace, init_api_key: str
) -> CreateTemplateRequest:
    # The agent server binds loopback unless a session API key is set at boot.
    # Under deferred init there is none, so the host is spelled out; the
    # dormant gate protects it until /api/init lands.
    start = (
        f'exec /usr/local/bin/openhands-agent-server --host 0.0.0.0 '
        f'--port {AGENT_SERVER_PORT}'
    )
    return CreateTemplateRequest(
        image=args.image,
        publish=args.publish,
        format='native',
        resource_limits={'cpu': args.cpu, 'memory': args.memory, 'disk': args.disk},
        entrypoint=['/bin/sh', '-c', start],
        env={'OH_DEFERRED_INIT': '1', 'OH_SECRET_KEY': init_api_key},
        readiness=TemplateReadiness(
            probe=f'tcp://127.0.0.1:{AGENT_SERVER_PORT}', warmup_seconds=120
        ),
        metadata={'purpose': 'openhands-agent-server'},
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=default_image())
    parser.add_argument(
        '--publish', required=True, help='Artifact store, e.g. s3://bucket/publish'
    )
    parser.add_argument('--cpu', default='2')
    parser.add_argument('--memory', default='4Gi')
    parser.add_argument('--disk', default='10Gi')
    parser.add_argument(
        '--init-api-key',
        default=os.getenv('OPENSANDBOX_INIT_API_KEY'),
        help='OH_SECRET_KEY baked into the template. Generated when omitted.',
    )
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()

    init_api_key = args.init_api_key or secrets.token_urlsafe(32)
    config = ConnectionConfig(
        domain=os.environ['OPENSANDBOX_DOMAIN'],
        protocol=os.getenv('OPENSANDBOX_PROTOCOL', 'http'),
        api_key=os.environ['OPENSANDBOX_API_KEY'],
    )
    async with await SandboxManager.create(config) as manager:
        template = await manager.create_template(template_request(args, init_api_key))
        print(f'Building template {template.template_id} from {args.image}')
        deadline = time.monotonic() + args.timeout
        while True:
            template = await manager.get_template(template.template_id)
            if template.status.phase == TemplatePhase.SUCCEEDED:
                break
            if template.status.phase == TemplatePhase.FAILED:
                print(f'Build failed: {template.status.message}', file=sys.stderr)
                return 1
            if time.monotonic() > deadline:
                print('Timed out waiting for the build', file=sys.stderr)
                return 1
            await asyncio.sleep(5)

    print(f'OPENSANDBOX_TEMPLATE_ID={template.template_id}')
    print(f'OPENSANDBOX_INIT_API_KEY={init_api_key}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
