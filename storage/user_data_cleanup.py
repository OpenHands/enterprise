"""User-only purge inside the caller's transaction; organizations are retained."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class UserCleanupConflict(RuntimeError):
    pass


async def delete_user_data(session: AsyncSession, user_id: UUID) -> None:
    params = {'uid': str(user_id)}
    await check_user_data_cleanup(session, user_id)
    await session.execute(
        text(
            'UPDATE conversation_metadata_saas c SET user_id = (SELECT m.user_id FROM org_member m WHERE m.org_id = c.org_id AND m.user_id != :uid ORDER BY m.user_id LIMIT 1) WHERE c.user_id = :uid'
        ),
        params,
    )
    statements = (
        'DELETE FROM jira_conversations WHERE jira_user_id IN (SELECT id FROM jira_users WHERE keycloak_user_id = :uid)',
        'DELETE FROM jira_dc_conversations WHERE jira_dc_user_id IN (SELECT id FROM jira_dc_users WHERE keycloak_user_id = :uid)',
        'DELETE FROM linear_conversations WHERE linear_user_id IN (SELECT id FROM linear_users WHERE keycloak_user_id = :uid)',
        'DELETE FROM conversation_metadata_saas WHERE user_id = :uid',
        'DELETE FROM daily_conversation_usage WHERE user_id = :uid',
        'UPDATE quota_increase_request SET approved_by_user_id = NULL WHERE approved_by_user_id = :uid',
        'DELETE FROM quota_increase_request WHERE user_id = :uid',
        'DELETE FROM conversation_work WHERE user_id = :uid',
        'DELETE FROM app_conversation_start_task WHERE created_by_user_id = :uid',
        'DELETE FROM feature_flag_rules WHERE user_id = :uid',
        'DELETE FROM user_settings WHERE keycloak_user_id = :uid',
        'DELETE FROM api_keys WHERE user_id = :uid',
        'DELETE FROM offline_tokens WHERE user_id = :uid',
        'DELETE FROM auth_tokens WHERE keycloak_user_id = :uid',
        'DELETE FROM device_codes WHERE keycloak_user_id = :uid',
        'DELETE FROM "user-repos" WHERE user_id = :uid',
        'DELETE FROM billing_sessions WHERE user_id = :uid',
        'DELETE FROM stripe_customers WHERE keycloak_user_id = :uid',
        'DELETE FROM subscription_access WHERE user_id = :uid',
        'UPDATE custom_secrets SET keycloak_user_id = NULL WHERE keycloak_user_id = :uid AND is_org_shared = true',
        'DELETE FROM custom_secrets WHERE keycloak_user_id = :uid',
        'DELETE FROM slack_conversation WHERE keycloak_user_id = :uid',
        'DELETE FROM slack_users WHERE keycloak_user_id = :uid',
        'DELETE FROM resend_synced_users WHERE keycloak_user_id = :uid',
        'DELETE FROM org_invitation WHERE inviter_id = :uid OR accepted_by_user_id = :uid',
        'DELETE FROM org_user_budget_override WHERE user_id = :uid',
        'DELETE FROM org_member WHERE user_id = :uid',
        'DELETE FROM jira_users WHERE keycloak_user_id = :uid',
        'DELETE FROM jira_dc_users WHERE keycloak_user_id = :uid',
        'DELETE FROM linear_users WHERE keycloak_user_id = :uid',
        'DELETE FROM bitbucket_webhook WHERE user_id = :uid',
        'DELETE FROM bitbucket_dc_webhook WHERE user_id = :uid',
        'DELETE FROM gitlab_webhook WHERE user_id = :uid',
    )
    for statement in statements:
        await session.execute(text(statement), params)
    await session.execute(text('DELETE FROM "user" WHERE id = :uid'), params)


async def check_user_data_cleanup(session: AsyncSession, user_id: UUID) -> None:
    params = {'uid': str(user_id)}
    # Separate workspace deletion owns its cascade and external team cleanup.
    if await session.scalar(text('SELECT 1 FROM org WHERE id = :uid'), params):
        raise UserCleanupConflict(
            'Delete or transfer the personal workspace before deleting this user'
        )
    if await session.scalar(
        text('SELECT 1 FROM v1_remote_sandbox WHERE created_by_user_id = :uid LIMIT 1'),
        params,
    ):
        raise UserCleanupConflict(
            'Delete user sandboxes through the sandbox service before deleting this user'
        )
    if await session.scalar(
        text(
            'SELECT 1 FROM conversation_metadata_saas c WHERE c.user_id = :uid AND NOT EXISTS (SELECT 1 FROM org_member m WHERE m.org_id = c.org_id AND m.user_id != :uid) LIMIT 1'
        ),
        params,
    ):
        raise UserCleanupConflict(
            'Transfer conversation ownership before deleting this user'
        )
    # Shared integration workspaces require an explicit administrator transfer.
    for table in ('jira_workspaces', 'jira_dc_workspaces', 'linear_workspaces'):
        if await session.scalar(
            text(f'SELECT 1 FROM {table} WHERE admin_user_id = :uid LIMIT 1'), params
        ):
            raise UserCleanupConflict(
                'Transfer integration workspace administration before deleting this user'
            )
    if await session.scalar(
        text('SELECT 1 FROM org_git_claim WHERE claimed_by = :uid LIMIT 1'), params
    ):
        raise UserCleanupConflict(
            'Transfer organization git claims before deleting this user'
        )
