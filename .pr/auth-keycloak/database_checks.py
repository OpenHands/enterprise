"""Seed at revision 157 and assert preserved account data after live login."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text

from storage.api_key import ApiKey
from storage.database import a_session_maker
from storage.encrypt_utils import encrypt_legacy_value
from storage.org import Org
from storage.org_member import OrgMember
from storage.user import User
from storage.user_settings import UserSettings

WORK = Path(__file__).resolve().parent
state = json.loads((WORK / 'state.json').read_text())


async def main():
    async with a_session_maker() as session:
        version = await session.scalar(text('SELECT version_num FROM alembic_version'))
        if '--seed' in sys.argv:
            assert version == '157', version
            assert (
                await session.scalar(select(func.count()).select_from(UserSettings))
                == 0
            )
            legacy = UserSettings(
                keycloak_user_id=state['legacy_user_id'],
                email=state['legacy_email'],
                email_verified=True,
                already_migrated=False,
                user_version=0,
                accepted_tos=datetime.now(UTC).replace(tzinfo=None),
                llm_api_key=encrypt_legacy_value(state['legacy_llm_secret']),
                agent_settings={
                    'llm': {
                        'model': 'openai/legacy-preserved',
                        'base_url': 'http://127.0.0.1:9/v1',
                    }
                },
                conversation_settings={},
                language='es',
                user_consents_to_analytics=False,
                v1_enabled=True,
            )
            key = ApiKey(
                user_id=state['legacy_user_id'],
                key=state['api_key'],
                name='Existing SDK key',
            )
            session.add_all([legacy, key])
            await session.commit()
            state.update({'legacy_settings_id': legacy.id, 'legacy_api_key_id': key.id})
            (WORK / 'state.json').write_text(json.dumps(state, indent=2))
            print(
                json.dumps(
                    {
                        'seed_revision': version,
                        'settings_only_user': state['legacy_user_id'],
                        'api_key_id': key.id,
                    }
                )
            )
            return
        assert version == '159', version
        installed_mode = await session.scalar(
            text('SELECT mode FROM installation_auth WHERE id = 1')
        )
        assert installed_mode == 'keycloak', installed_mode
        assert await session.scalar(text('SELECT count(*) FROM local_credentials')) == 0
        if '--broker-state' in sys.argv:
            rows = (
                await session.execute(
                    text(
                        'SELECT id,email,current_org_id FROM "user" WHERE email LIKE :email'
                    ),
                    {'email': '%@keycloak-qa.example'},
                )
            ).all()
            offline = (
                (await session.execute(text('SELECT user_id FROM offline_tokens')))
                .scalars()
                .all()
            )
            provider_tokens = await session.scalar(
                text('SELECT count(*) FROM auth_tokens')
            )
            print(
                json.dumps(
                    {
                        'broker_accounts': [
                            {
                                'id': str(row.id),
                                'email': row.email,
                                'org_id': str(row.current_org_id),
                                'offline_token_present': str(row.id) in offline,
                            }
                            for row in rows
                        ],
                        'provider_token_rows': provider_tokens,
                        'local_credentials': 0,
                    }
                )
            )
            assert provider_tokens == 0
            return
        if '--before-login' in sys.argv:
            assert await session.get(User, UUID(state['legacy_user_id'])) is None
            assert not (
                await session.get(UserSettings, state['legacy_settings_id'])
            ).already_migrated
            print(
                json.dumps(
                    {
                        'migrated_schema': version,
                        'persisted_mode': installed_mode,
                        'canonical_user_exists': False,
                    }
                )
            )
            return
        user = await session.get(User, UUID(state['legacy_user_id']))
        org = await session.get(Org, UUID(state['legacy_user_id']))
        member = await session.scalar(
            select(OrgMember).where(
                OrgMember.user_id == user.id, OrgMember.org_id == org.id
            )
        )
        key = await session.get(ApiKey, state['legacy_api_key_id'])
        assert (
            str(user.id)
            == str(org.id)
            == str(user.current_org_id)
            == str(member.org_id)
            == state['legacy_user_id']
        )
        assert (
            user.email == state['legacy_email']
            and user.email_verified
            and user.language == 'es'
        )
        assert (
            key.user_id == str(user.id)
            and key.org_id == org.id
            and key.key == state['api_key']
        )
        assert member.agent_settings_diff['llm']['model'] == 'openai/legacy-preserved'
        assert member.agent_settings_diff['llm']['base_url'] == 'http://127.0.0.1:9/v1'
        assert member.llm_api_key.get_secret_value() == state['legacy_llm_secret']
        assert (
            await session.get(UserSettings, state['legacy_settings_id'])
        ).already_migrated
        identities = (
            await session.execute(
                text(
                    'SELECT connection, subject FROM external_identities WHERE user_id = :uid'
                ),
                {'uid': user.id},
            )
        ).all()
        assert ('keycloak', state['legacy_user_id']) in identities
        print(
            json.dumps(
                {
                    'schema_revision': version,
                    'persisted_mode': installed_mode,
                    'stable_user_org_id': str(user.id),
                    'stable_api_key_id': key.id,
                    'legacy_settings_id': state['legacy_settings_id'],
                    'language_preserved': True,
                    'model_base_url_encrypted_key_preserved': True,
                    'external_identity_linked': True,
                    'local_credentials': 0,
                }
            )
        )


asyncio.run(main())
