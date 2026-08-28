from __future__ import annotations

import argparse
import asyncio

from openhands.cli.org_migrate import register_org_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='openhands')
    subparsers = parser.add_subparsers(dest='command')
    register_org_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, 'func'):
        parser.print_help()
        return 1

    result = args.func(args)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    if isinstance(result, int):
        return result
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
