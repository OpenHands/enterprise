"""Operator-only administration (run inside the app image with its DB/encryption config).

No HTTP route exists for key writes: sandbox-held session keys and full-user API keys
can therefore never manage customer mappings. Keys are read from stdin, never argv.

  python -m server.customer_gateway.cli set-org --org ORG --gateway-id ID --profile-file F [--disable]
  python -m server.customer_gateway.cli set-key --org ORG --user USER --models a,b  < key
  python -m server.customer_gateway.cli disable|enable --org ORG --user USER
  python -m server.customer_gateway.cli status --org ORG
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
from uuid import UUID

from server.customer_gateway.config import env_flag
from server.customer_gateway.profile import parse_profile
from storage.customer_gateway_store import CustomerGatewayStore


def _actor() -> str:
    return 'operator-cli:' + os.environ.get(
        'CUSTOMER_GATEWAY_OPERATOR', getpass.getuser()
    )


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog='customer_gateway')
    sub = ap.add_subparsers(dest='cmd', required=True)
    o = sub.add_parser('set-org')
    o.add_argument('--org', type=UUID, required=True)
    o.add_argument('--gateway-id', required=True)
    o.add_argument('--profile-file', required=True)
    o.add_argument('--disable', action='store_true')
    k = sub.add_parser('set-key')
    k.add_argument('--org', type=UUID, required=True)
    k.add_argument('--user', type=UUID, required=True)
    k.add_argument('--models', required=True)
    for name in ('disable', 'enable'):
        d = sub.add_parser(name)
        d.add_argument('--org', type=UUID, required=True)
        d.add_argument('--user', type=UUID, required=True)
    st = sub.add_parser('status')
    st.add_argument('--org', type=UUID, required=True)
    a = ap.parse_args(argv)
    if a.cmd == 'set-org':
        with open(a.profile_file) as f:
            raw = json.load(f)
        parse_profile(
            raw,
            allow_http=env_flag('CUSTOMER_GATEWAY_ALLOW_HTTP_TEST_ONLY'),
        )
        await CustomerGatewayStore.set_org_gateway(
            a.org, a.gateway_id, raw, not a.disable, _actor()
        )
        print(json.dumps({'org': str(a.org), 'enabled': not a.disable}))
    elif a.cmd == 'set-key':
        key = sys.stdin.readline().strip()
        models = [m.strip() for m in a.models.split(',')]
        if not models or any(not m for m in models):
            raise SystemExit(
                f'--models {a.models!r} has an empty entry; '
                'use a comma-separated list of non-empty catalog aliases'
            )
        gw = await CustomerGatewayStore.get_org_gateway(a.org)
        if gw is None:
            raise SystemExit(f'no gateway config found for org {a.org}')
        profile = parse_profile(
            gw.profile, allow_http=env_flag('CUSTOMER_GATEWAY_ALLOW_HTTP_TEST_ONLY')
        )
        unknown = [m for m in models if m not in profile.models]
        if unknown:
            raise SystemExit(
                f'--models has aliases not in the org catalog: {unknown}; '
                f'known aliases: {sorted(profile.models)}'
            )
        v = await CustomerGatewayStore.set_member_credential(
            a.org, a.user, key, models, _actor()
        )
        print(json.dumps({'user': str(a.user), 'credential_version': v}))
    elif a.cmd in ('disable', 'enable'):
        v = await CustomerGatewayStore.set_member_enabled(
            a.org, a.user, a.cmd == 'enable', _actor()
        )
        print(json.dumps({'user': str(a.user), 'policy_version': v}))
    else:
        print(json.dumps(await CustomerGatewayStore.masked_status(a.org), default=str))
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main(sys.argv[1:])))
