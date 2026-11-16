"""Audit stored profile compatibility without changing any rows."""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from sqlalchemy import text

from openhands.sdk.profiles import validate_agent_profile
from openhands.sdk.profiles.agent_profile import AGENT_PROFILE_SCHEMA_VERSION


def audit_envelope(org_id: str, envelope: Any) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not isinstance(envelope, dict) or not isinstance(
        envelope.get('profiles', {}), dict
    ):
        return [{'org_id': org_id, 'kind': 'unreadable_org'}]
    profiles = envelope.get('profiles', {})
    for profile_id, raw in profiles.items():
        finding = {'org_id': org_id, 'profile_id': str(profile_id)}
        version = raw.get('schema_version', 1) if isinstance(raw, dict) else None
        if type(version) is not int or version < 1:
            findings.append({**finding, 'kind': 'unreadable_profile'})
            continue
        if type(version) is int and version > AGENT_PROFILE_SCHEMA_VERSION:
            findings.append({**finding, 'kind': 'future_profile'})
            continue
        try:
            validate_agent_profile(copy.deepcopy(raw))
        except Exception:
            findings.append({**finding, 'kind': 'unreadable_profile'})
            continue
        if (
            type(version) is int
            and version in (1, 2)
            and raw.get('agent_kind', 'openhands') == 'openhands'
            and raw.get('tools') == []
        ):
            findings.append({**finding, 'kind': 'legacy_empty_tools'})
    active = envelope.get('active')
    if active is not None and active not in profiles:
        findings.append(
            {
                'org_id': org_id,
                'profile_id': str(active),
                'kind': 'missing_active_profile',
            }
        )
    return findings


def main() -> int:
    from storage.database import session_maker
    from storage.encrypt_utils import decrypt_value

    findings: list[dict[str, str]] = []
    scanned = 0
    with session_maker() as session:
        rows = session.execute(
            text('SELECT id, agent_profiles FROM org WHERE agent_profiles IS NOT NULL')
        ).mappings()
        for row in rows:
            scanned += 1
            try:
                envelope = json.loads(decrypt_value(row['agent_profiles']))
            except Exception:
                findings.append({'org_id': str(row['id']), 'kind': 'unreadable_org'})
                continue
            findings.extend(audit_envelope(str(row['id']), envelope))
    counts = Counter(item['kind'] for item in findings)
    print(
        json.dumps(
            {'scanned_orgs': scanned, 'counts': dict(counts), 'findings': findings},
            indent=2,
        )
    )
    return 2 if any(item['kind'] != 'legacy_empty_tools' for item in findings) else 0


if __name__ == '__main__':
    raise SystemExit(main())
