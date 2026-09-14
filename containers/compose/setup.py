"""Create private, persistent local credentials for the Enterprise Compose stack."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path


class Arguments(argparse.Namespace):
    output: Path


def docker_socket_path() -> str:
    default = '/var/run/docker.sock'
    docker_context = os.environ.get('DOCKER_CONTEXT')
    docker_host = os.environ.get('DOCKER_HOST')
    if docker_context or not docker_host:
        try:
            command = [
                'docker',
                'context',
                'inspect',
                '--format',
                '{{.Endpoints.docker.Host}}',
            ]
            if docker_context:
                command.append(docker_context)
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            docker_host = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return default
    if docker_host.startswith('unix://'):
        path = docker_host.removeprefix('unix://')
        if Path(path).is_absolute() and '\n' not in path and '\r' not in path:
            return path
    return default


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output',
        type=Path,
        default=Path(__file__).resolve().parents[2] / '.env',
        help='Environment file to create (default: repository root .env)',
    )
    args = parser.parse_args(namespace=Arguments())
    values = {
        'DB_PASSWORD': secrets.token_hex(24),
        'JWT_SECRET': secrets.token_hex(32),
        'SUPERADMIN_PASSWORD': secrets.token_urlsafe(24),
    }
    template = Path(__file__).with_name('.env.example').read_text()
    for name, value in values.items():
        template = template.replace(f'{name}=\n', f'{name}={value}\n')
    template = template.replace(
        'DOCKER_SOCKET_PATH=/var/run/docker.sock\n',
        f'DOCKER_SOCKET_PATH={docker_socket_path()}\n',
    )
    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.exit(
            message=f'{args.output} already exists; keeping its credentials unchanged.\n'
        )
    with os.fdopen(fd, 'w') as stream:
        stream.write(template)
    print(f'Created {args.output} with private permissions.')
    print('Edit this file to choose the login email, app port, or Docker socket.')
    print('The generated login password is stored as SUPERADMIN_PASSWORD in that file.')


if __name__ == '__main__':
    main()
