"""Real-Postgres integration test for migration 170's write-back path.

Exercises the exact scenario the reviewer flagged: an org whose ``llm_profiles``
was materialized with the bogus ``openhands/deepseek-v4-flash`` Default while
158/160's verified_models row was live, then migration 170 repairs it, then the
ORM (what the app does) decrypts ``org.llm_profiles`` and loads the expected
Default.

This is NOT exercised by a fresh clean-install (no corrupted org exists), so it
runs against a real postgres:16 with the full migration chain, real
EncryptedJSON encrypt/decrypt, and the real postgres dialect result_processor
(psycopg2 and pg8000).

Usage:
  DB_PORT=5433 DB_DRIVER=''  uv run python3 scripts/verify_migration_170_writeback.py   # psycopg2
  DB_PORT=5433 DB_DRIVER=pg8000 uv run python3 scripts/verify_migration_170_writeback.py  # pg8000
"""

import json
import os
import sys
import uuid

# uv runs this with package=false, so the repo root isn't on sys.path by default.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from storage.encrypt_utils import decrypt_value
from storage.org import Org

# Self-hosted: WEB_HOST unset -> 170 runs, 158/160 seed the deepseek row.
os.environ.pop('WEB_HOST', None)

# Stable encryption key: get_default_encryption_keys() derives a deterministic
# key from JWT_SECRET; without it, fallback keys aren't stable across calls and
# decrypt fails. Migration 170 itself calls encrypt_value/decrypt_value, so this
# must be set before alembic runs too.
os.environ.setdefault('JWT_SECRET', 'migration-170-writeback-test-secret')

# alembic env.py re-derives the URL from these env vars (not sqlalchemy.url),
# so export them for both the alembic command and our own engine.
os.environ.setdefault('DB_USER', 'openhands')
os.environ.setdefault('DB_PASS', 'openhands')
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('DB_PORT', '5433')
os.environ.setdefault('DB_NAME', 'openhands')
os.environ.setdefault('DB_DRIVER', '')  # '' = psycopg2

DB_USER = os.environ['DB_USER']
DB_PASS = os.environ['DB_PASS']
DB_HOST = os.environ['DB_HOST']
DB_PORT = os.environ['DB_PORT']
DB_NAME = os.environ['DB_NAME']
DB_DRIVER = os.environ['DB_DRIVER']

MANAGED_DEFAULT = 'openhands/deepseek-v4-flash'
BYOK_MODEL = 'anthropic/claude-3-5-sonnet'
BYOK_KEY = 'sk-ant-byok-xxxxx'


def _url() -> str:
    scheme = f'postgresql+{DB_DRIVER}' if DB_DRIVER else 'postgresql'
    return f'{scheme}://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'


def _raw_decrypt(raw: str | None) -> dict | None:
    if raw is None:
        return None
    return json.loads(decrypt_value(raw))


def _profiles(default_model: str | None, active: str | None = 'Default') -> dict:
    profs = {}
    if default_model is not None:
        profs['Default'] = {'model': default_model}
    return {'profiles': profs, 'active': active}


def main() -> int:
    eng = create_engine(_url(), future=True)

    print(f'\n=== driver={DB_DRIVER or "psycopg2"} ===')

    # 1) Apply migrations up to 169 (NOT 170). 158/160 seed the deepseek row.
    print('[1] alembic upgrade 169 ...')
    from alembic.config import Config
    from alembic import command

    cfg = Config('alembic.ini')
    cfg.set_main_option('sqlalchemy.url', _url())
    command.upgrade(cfg, '169')

    # 2) Confirm the bogus verified_models row is present.
    with eng.connect() as c:
        n = c.execute(
            text(
                "SELECT count(*) FROM verified_models "
                "WHERE provider='openhands' AND model_name='deepseek-v4-flash'"
            )
        ).scalar()
    print(f'[2] deepseek verified_models rows before 170: {n}')
    assert n == 1, 'precondition: 158/160 should have seeded the deepseek row'

    # 3) Seed three orgs with REAL encrypted llm_profiles (via ORM bind).
    org_restore = Org(
        name=f'restore-{uuid.uuid4().hex[:8]}',
        agent_settings={
            'llm': {'model': BYOK_MODEL, 'api_key': BYOK_KEY, 'base_url': 'https://api.anthropic.com'}
        },
        llm_profiles=_profiles(MANAGED_DEFAULT),  # bogus baked Default + BYOK legacy
    )
    org_strip = Org(
        name=f'strip-{uuid.uuid4().hex[:8]}',
        agent_settings={},  # no legacy BYOK model -> strip the phantom Default
        llm_profiles=_profiles(MANAGED_DEFAULT),
    )
    org_noop = Org(
        name=f'noop-{uuid.uuid4().hex[:8]}',
        agent_settings={},
        llm_profiles=_profiles('anthropic/claude'),  # concrete, non-bogus Default
    )
    with Session(eng) as s:
        s.add_all([org_restore, org_strip, org_noop])
        s.commit()
        ids = {
            'restore': org_restore.id,
            'strip': org_strip.id,
            'noop': org_noop.id,
        }

    # Confirm the DB actually holds ciphertext (not JSON) for these orgs.
    with eng.connect() as c:
        raw = c.execute(
            text('SELECT llm_profiles FROM org WHERE id=:i'), {'i': str(ids['restore'])}
        ).scalar()
    print(f'[3] seeded orgs; raw llm_profiles[restore] is {len(raw)}-char ciphertext, valid JSON? ', end='')
    try:
        json.loads(raw)
        print('YES (unexpected!)')
    except Exception:
        print('NO (ciphertext, as expected)')

    # 4) Run migration 170 against the pre-seeded corrupted orgs.
    print('[4] alembic upgrade head (run migration 170) ...')
    command.upgrade(cfg, 'head')

    # 5) Verify deepseek verified_models row deleted.
    with eng.connect() as c:
        n = c.execute(
            text(
                "SELECT count(*) FROM verified_models "
                "WHERE provider='openhands' AND model_name='deepseek-v4-flash'"
            )
        ).scalar()
    print(f'[5] deepseek verified_models rows after 170: {n}')
    assert n == 0, '170 should have deleted the deepseek verified_models row'

    # 6) Verify the write-back: ORM read (app's path) decrypts + loads expected Default.
    failures = 0
    with Session(eng) as s:
        for bucket, oid in ids.items():
            org = s.get(Org, oid)
            # ORM EncryptedJSON.process_result_value already decrypted for us:
            dec = org.llm_profiles
            print(f'[6] {bucket:8} llm_profiles (ORM-decrypted) = {json.dumps(dec)}')
            if bucket == 'restore':
                d = (dec or {}).get('profiles', {}).get('Default', {})
                ok = d.get('model') == BYOK_MODEL and d.get('api_key') == BYOK_KEY
                ok = ok and dec.get('active') == 'Default'
                print(f'         expected Default restored to {BYOK_MODEL} -> {"PASS" if ok else "FAIL"}')
            elif bucket == 'strip':
                ok = 'Default' not in (dec or {}).get('profiles', {}) and (dec or {}).get('active') is None
                print(f'         expected phantom Default stripped -> {"PASS" if ok else "FAIL"}')
            elif bucket == 'noop':
                d = (dec or {}).get('profiles', {}).get('Default', {})
                ok = d.get('model') == 'anthropic/claude' and dec.get('active') == 'Default'
                print(f'         expected untouched -> {"PASS" if ok else "FAIL"}')
            if not ok:
                failures += 1

    # 7) Cross-check: raw decrypt matches ORM decrypt (no double-encoding on write-back).
    with eng.connect() as c:
        raw_restore = c.execute(
            text('SELECT llm_profiles FROM org WHERE id=:i'), {'i': str(ids['restore'])}
        ).scalar()
    raw_dec = _raw_decrypt(raw_restore)
    with Session(eng) as s:
        orm_dec = s.get(Org, ids['restore']).llm_profiles
    roundtrip_ok = raw_dec == orm_dec
    print(f'[7] raw-decrypt == ORM-decrypt (no write-back double-encoding): {"PASS" if roundtrip_ok else "FAIL"}')
    if not roundtrip_ok:
        failures += 1

    print(f'\n=== {"ALL PASS" if failures == 0 else f"{failures} FAILURE(S)"} (driver={DB_DRIVER or "psycopg2"}) ===')
    return 1 if failures else 0


if __name__ == '__main__':
    import traceback

    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
