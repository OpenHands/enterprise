#!/usr/bin/env python
"""Build the E2B template the E2B sandbox backend starts sandboxes from.

The template layers the deferred-init agent server on top of the officially
published agent-server image, so a sandbox created from it boots straight into
a listening, dormant uvicorn - `AsyncSandbox.create()` resumes a memory
snapshot of an already-started server rather than installing anything.

    export E2B_API_KEY=...
    export E2B_DOMAIN=...          # e.g. e2b.app, or your self hosted domain
    export E2B_API_URL=...         # self hosted clusters only
    uv run scripts/e2b/build_template.py

The init key it prints is the `X-Init-API-Key` the app server must present on
`POST /api/init`; set it as `E2B_INIT_API_KEY` wherever the app server runs.
Pass `--init-api-key` to rebuild an existing template without rotating it.

Template builds on a self hosted cluster are worth treating as retryable: an
`internal error occurred` failure part way through generally succeeds on an
identical re-run.
"""

import argparse
import asyncio
import base64
import os
import secrets
import sys
from importlib.metadata import version

from e2b import AsyncTemplate, wait_for_port
from e2b.template.main import TemplateFinal

DEFAULT_TEMPLATE_NAME = 'openhands-agent-server'
DEFAULT_IMAGE_REPOSITORY = 'ghcr.io/openhands/agent-server'
AGENT_SERVER_PORT = 8000


def default_image() -> str:
    """The published agent-server image matching the installed SDK."""
    return f'{DEFAULT_IMAGE_REPOSITORY}:{version("openhands-agent-server")}-python'


def build_template(image: str, init_api_key: str) -> TemplateFinal:
    start_cmd = (
        'OH_DEFERRED_INIT=1 '
        f'OH_SECRET_KEY={init_api_key} '
        # The agent server binds loopback unless a session API key is
        # configured at boot, and under deferred init there is none, so the
        # bind host has to be spelled out or nothing outside the sandbox can
        # reach it. The dormant gate is what protects it until /api/init lands.
        f'/usr/local/bin/openhands-agent-server --host 0.0.0.0 --port {AGENT_SERVER_PORT}'
    )
    return (
        AsyncTemplate()
        .from_image(image)
        # The image runs as `openhands` and E2B's base layer sets its own non
        # root default, so apt needs an explicit switch to root.
        .set_user('root')
        # `wait_for_port` shells out to `ss`, which the published image does
        # not ship.
        .run_cmd('apt-get update -qq && apt-get install -y -qq iproute2')
        .set_envs({'OH_DEFERRED_INIT': '1', 'OH_SECRET_KEY': init_api_key})
        .set_start_cmd(start_cmd, wait_for_port(AGENT_SERVER_PORT))
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default=DEFAULT_TEMPLATE_NAME)
    parser.add_argument(
        '--image',
        default=None,
        help='Base image. Defaults to the published agent-server image for the '
        'installed openhands-agent-server version.',
    )
    parser.add_argument(
        '--init-api-key',
        default=os.getenv('E2B_INIT_API_KEY'),
        help='OH_SECRET_KEY baked into the template. Generated when omitted.',
    )
    parser.add_argument('--cpu-count', type=int, default=2)
    # 2048 is the size this template has been exercised at end to end. Raising
    # it is only useful if the cluster's nodes can still fit the sandbox: E2B
    # rejects a create it cannot place, and that failure is permanent, not
    # transient.
    parser.add_argument('--memory-mb', type=int, default=2048)
    args = parser.parse_args()

    image = args.image or default_image()
    init_api_key = args.init_api_key or base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode().rstrip('=')

    print(f'Building template {args.name!r} from {image}')
    info = await AsyncTemplate.build(
        build_template(image, init_api_key),
        name=args.name,
        cpu_count=args.cpu_count,
        memory_mb=args.memory_mb,
        on_build_logs=lambda entry: print(f'  {entry.message}'),
    )
    print(f'\nBuilt {info.name} (template_id={info.template_id})')
    print('\nConfigure the app server with:')
    print('  RUNTIME=e2b')
    print(f'  E2B_TEMPLATE={args.name}')
    print(f'  E2B_INIT_API_KEY={init_api_key}')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
