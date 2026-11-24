#!/usr/bin/env python
# /// script
# requires-python = ">=3.12"
# dependencies = ["grpcio-tools==1.71.0"]
# ///
"""Generate the Python gRPC client for Agent Substrate's control API.

Substrate ships no Python SDK, so the Substrate sandbox backend calls
ate-api-server through stubs generated from Substrate's own ``ateapi.proto``:

    uv run scripts/substrate/generate_ateapi.py

``grpcio-tools`` 1.71 bundles the protoc that matches the ``protobuf`` 5.29
runtime in ``uv.lock``. Generated code must not need a newer runtime than the
one installed, so move both together.
"""

import argparse
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

SUBSTRATE_VERSION = 'v1.0.1'
PROTO_URL = (
    'https://raw.githubusercontent.com/agent-substrate/substrate/'
    '{version}/api/ateapi/v1/ateapi.proto'
)
OUTPUT_DIR = (
    Path(__file__).resolve().parents[2] / 'openhands/app_server/sandbox/substrate'
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--version', default=SUBSTRATE_VERSION)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as proto_dir:
        proto = Path(proto_dir) / 'ateapi.proto'
        with urllib.request.urlopen(PROTO_URL.format(version=args.version)) as resp:
            proto.write_bytes(resp.read())
        subprocess.run(
            [
                sys.executable,
                '-m',
                'grpc_tools.protoc',
                f'-I{proto_dir}',
                f'--python_out={OUTPUT_DIR}',
                f'--pyi_out={OUTPUT_DIR}',
                f'--grpc_python_out={OUTPUT_DIR}',
                str(proto),
            ],
            check=True,
        )

    # protoc writes an absolute import, which only works with the output
    # directory on sys.path.
    grpc_module = OUTPUT_DIR / 'ateapi_pb2_grpc.py'
    grpc_module.write_text(
        grpc_module.read_text().replace(
            'import ateapi_pb2 as ateapi__pb2',
            'from . import ateapi_pb2 as ateapi__pb2',
        )
    )
    print(f'Generated the ateapi {args.version} client in {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
