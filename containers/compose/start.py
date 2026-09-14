"""Configure the Compose managed sandbox catalog and start Enterprise services."""

import argparse
import json
import os
import subprocess
import sys
from typing import NotRequired, TypedDict


class DockerSettings(TypedDict):
    network: str
    bind_host: str
    container_url_pattern: str
    webhook_url: str
    extra_hosts: dict[str, str]
    workspace_mount_path: str
    ports: dict[str, int]
    public_url_pattern: NotRequired[str]


class SandboxTemplate(TypedDict):
    id: str
    image: str
    working_dir: str
    initial_env: dict[str, str]
    docker: DockerSettings


class Arguments(argparse.Namespace):
    command: str


def configure_sandboxes() -> None:
    from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_image

    image = os.environ.get('COMPOSE_AGENT_SERVER_IMAGE') or get_agent_server_image()
    callback_url = os.environ['OH_SANDBOX_CALLBACK_URL'].rstrip('/')
    public_domain = os.environ.get('COMPOSE_SANDBOX_DOMAIN')
    template: SandboxTemplate = {
        'id': 'python',
        'image': image,
        'working_dir': '/workspace/project',
        'initial_env': {
            'OH_ENABLE_VNC': '0',
            'OPENVSCODE_SERVER_ROOT': '/openhands/.openvscode-server',
        },
        'docker': {
            'network': os.environ['COMPOSE_SANDBOX_NETWORK'],
            'bind_host': '127.0.0.1' if public_domain else '0.0.0.0',
            'container_url_pattern': 'http://localhost:{port}',
            'webhook_url': f'{callback_url}/api/v1/webhooks',
            'extra_hosts': {'host.docker.internal': 'host-gateway'},
            'workspace_mount_path': '/workspace',
            'ports': {
                'AGENT_SERVER': 8000,
                'VSCODE': 8001,
                'WORKER_1': 8011,
                'WORKER_2': 8012,
            },
        },
    }
    if public_domain:
        template['docker']['public_url_pattern'] = (
            'https://{container_port}-{resource_id}.' + public_domain
        )
    os.environ['SANDBOX_TEMPLATES'] = json.dumps([template])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'serve'))
    args = parser.parse_args(namespace=Arguments())
    configure_sandboxes()
    if args.command == 'init':
        subprocess.run(['alembic', 'upgrade', 'head'], check=True)
        os.execv(sys.executable, [sys.executable, '-m', 'server.auth.bootstrap'])
    os.execvp(
        'uvicorn',
        ['uvicorn', 'saas_server:app', '--host', '0.0.0.0', '--port', '3000'],
    )


if __name__ == '__main__':
    main()
