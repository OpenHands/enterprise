"""Create persistent local MockSAML signing credentials without changing .env."""

import argparse
import base64
import os
import subprocess
import tempfile
from pathlib import Path


class Arguments(argparse.Namespace):
    output_dir: Path


def openssl(*arguments: str) -> bytes:
    return subprocess.run(
        ['openssl', *arguments], capture_output=True, check=True, timeout=60
    ).stdout


def environment(certificate: bytes, private_key: bytes) -> str:
    return (
        f'PUBLIC_KEY={base64.b64encode(certificate).decode("ascii")}\n'
        f'PRIVATE_KEY={base64.b64encode(private_key).decode("ascii")}\n'
    )


def validate(directory: Path) -> None:
    certificate = directory / 'idp.crt'
    private_key = directory / 'idp.key'
    env_file = directory / 'mocksaml.env'
    if directory.is_symlink() or any(
        path.is_symlink() or not path.is_file()
        for path in (certificate, private_key, env_file)
    ):
        raise ValueError(
            'Existing MockSAML credentials are incomplete or use symlinks.'
        )
    openssl('x509', '-in', str(certificate), '-checkend', '0', '-noout')
    if openssl('x509', '-in', str(certificate), '-pubkey', '-noout') != openssl(
        'pkey', '-in', str(private_key), '-pubout'
    ):
        raise ValueError('Existing MockSAML certificate and private key do not match.')
    if env_file.read_text() != environment(
        certificate.read_bytes(), private_key.read_bytes()
    ):
        raise ValueError(
            'Existing MockSAML environment does not match its certificate.'
        )
    directory.chmod(0o700)
    private_key.chmod(0o600)
    env_file.chmod(0o600)


def setup(directory: Path) -> bool:
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        validate(directory)
        return False

    # Generate all files before exposing them to Compose. Existing credentials
    # are validated above and are never replaced on a later setup invocation.
    try:
        with tempfile.TemporaryDirectory(prefix='.credentials-', dir=directory) as work:
            temporary = Path(work)
            certificate = temporary / 'idp.crt'
            private_key = temporary / 'idp.key'
            openssl(
                'req',
                '-x509',
                '-newkey',
                'rsa:3072',
                '-nodes',
                '-sha256',
                '-days',
                '3650',
                '-subj',
                '/CN=OpenHands Compose MockSAML',
                '-keyout',
                str(private_key),
                '-out',
                str(certificate),
            )
            env_file = temporary / 'mocksaml.env'
            with os.fdopen(
                os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w'
            ) as stream:
                stream.write(
                    environment(certificate.read_bytes(), private_key.read_bytes())
                )
            private_key.chmod(0o600)
            certificate.chmod(0o644)
            validate(temporary)
            for filename in ('idp.crt', 'idp.key', 'mocksaml.env'):
                (temporary / filename).rename(directory / filename)
    except BaseException:
        # Only remove the directory this call created, and only if still empty.
        # A failed first attempt can then be retried after installing OpenSSL.
        try:
            directory.rmdir()
        except OSError:
            pass
        raise
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(__file__).resolve().parents[2] / '.mocksaml',
        help='Credential directory to create (default: repository root .mocksaml)',
    )
    args = parser.parse_args(namespace=Arguments())
    try:
        created = setup(args.output_dir.expanduser().absolute())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(
            status=1,
            message=(
                f'MockSAML setup failed ({type(error).__name__}). '
                'Check that OpenSSL is installed and the credential directory is '
                'writable and complete. Existing credentials were not replaced.\n'
            ),
        )
    print(
        'Created private MockSAML credentials.'
        if created
        else 'Kept existing MockSAML credentials.'
    )
    print('See containers/compose/SAML.md to start the optional SAML stack.')


if __name__ == '__main__':
    main()
