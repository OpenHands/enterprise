"""Add the scheduled-job (R1) schema for the CronJob migration (PLTF-3689).

Creates the tables, columns and triggers the CronJob -> Procrastinate
migration needs. Nothing reads or writes them yet: both permits start closed
and the bootstrap starts ``pending``, so the seven CronJobs keep running as
today. The GitLab trigger only refuses writes once ``gitlab_handover_at`` is
set, which nothing sets before the R1 bootstrap.

Revision ID: 177
Revises: 176
Create Date: 2026-10-06 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = '177'
down_revision: str | None = '176'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)
NOW = sa.text('now()')
MODES = "('cronjob', 'worker')"
CLAIMED_TABLES = ('maintenance_tasks', 'gitlab_webhook', 'resend_synced_users')
APPEND_ONLY_TABLES = ('scheduled_job_pod', 'scheduled_job_pre_r0_pod')

# One statement per execute: pg8000 refuses multi-statement strings.
TRIGGERS = (
    """
    CREATE FUNCTION scheduled_job_pod_permanent() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scheduled_job_pod rows are never deleted';
      END IF;
      IF OLD.retired_at IS NOT NULL AND NEW.retired_at IS NULL THEN
        RAISE EXCEPTION 'scheduled_job_pod.retired_at is never cleared';
      END IF;
      RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER scheduled_job_pod_permanent
      BEFORE UPDATE OR DELETE ON scheduled_job_pod
      FOR EACH ROW EXECUTE FUNCTION scheduled_job_pod_permanent()
    """,
    """
    CREATE FUNCTION scheduled_job_pre_r0_pod_resolution_final() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'scheduled_job_pre_r0_pod rows are never deleted';
      END IF;
      IF OLD.resolution IS NOT NULL AND (
           NEW.resolution IS DISTINCT FROM OLD.resolution
           OR NEW.resolved_by IS DISTINCT FROM OLD.resolved_by
           OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at) THEN
        RAISE EXCEPTION 'scheduled_job_pre_r0_pod resolution is final once set';
      END IF;
      RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER scheduled_job_pre_r0_pod_resolution_final
      BEFORE UPDATE OR DELETE ON scheduled_job_pre_r0_pod
      FOR EACH ROW EXECUTE FUNCTION scheduled_job_pre_r0_pod_resolution_final()
    """,
    """
    CREATE FUNCTION gitlab_webhook_secret_moved() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.webhook_secret IS NOT NULL AND EXISTS (
           SELECT 1 FROM scheduled_job_bootstrap
            WHERE gitlab_handover_at IS NOT NULL) THEN
        RAISE EXCEPTION
          'webhook_secret is moved to webhook_secret_v2 (GitLab handover committed)';
      END IF;
      RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER gitlab_webhook_secret_moved
      BEFORE INSERT OR UPDATE ON gitlab_webhook
      FOR EACH ROW EXECUTE FUNCTION gitlab_webhook_secret_moved()
    """,
)


def _check(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=name)


def upgrade() -> None:
    op.create_table(
        'scheduled_job_permit',
        sa.Column('mode', sa.String(), primary_key=True),
        sa.Column('epoch', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('open', sa.Boolean(), nullable=False, server_default=sa.false()),
        _check('ck_scheduled_job_permit_mode', f'mode IN {MODES}'),
    )
    op.execute(
        'INSERT INTO scheduled_job_permit (mode, epoch, open) '
        "VALUES ('cronjob', 0, false), ('worker', 0, false)"
    )

    op.create_table(
        'scheduled_job_pod',
        sa.Column('pod_uid', sa.String(), primary_key=True),
        sa.Column('retired_at', TZ, nullable=True),
    )

    op.create_table(
        'scheduled_job_process',
        sa.Column('process_id', UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'pod_uid',
            sa.String(),
            sa.ForeignKey('scheduled_job_pod.pod_uid'),
            nullable=False,
        ),
        sa.Column('pod_name', sa.String(), nullable=False),
        sa.Column('node', sa.String(), nullable=True),
        sa.Column('container_name', sa.String(), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(), nullable=False),
        sa.Column('boot_at', TZ, nullable=False, server_default=NOW),
        sa.Column('last_seen', TZ, nullable=False, server_default=NOW),
        sa.Column('exited_at', TZ, nullable=True),
        sa.UniqueConstraint(
            'pod_uid',
            'container_name',
            'generation',
            name='uq_scheduled_job_process_pod_container_generation',
        ),
        _check('ck_scheduled_job_process_mode', f'mode IN {MODES}'),
    )

    op.create_table(
        'scheduled_job_observation',
        sa.Column('container_id', sa.String(), primary_key=True),
        sa.Column('pod_uid', sa.String(), nullable=False, index=True),
        sa.Column('container_name', sa.String(), nullable=False),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('resource_version', sa.String(), nullable=True),
        sa.Column('finished_at', TZ, nullable=True),
        sa.Column('observed_at', TZ, nullable=False, server_default=NOW),
    )

    op.create_table(
        'scheduled_job_occurrence',
        sa.Column('job', sa.String(), primary_key=True),
        sa.Column('occurrence', TZ, primary_key=True),
        sa.Column('state', sa.String(), nullable=False, server_default='open'),
        sa.Column('cursor', JSONB(), nullable=True),
        sa.Column('created_at', TZ, nullable=False, server_default=NOW),
        sa.Column('completed_at', TZ, nullable=True),
        _check(
            'ck_scheduled_job_occurrence_state',
            "state IN ('open', 'completed', 'superseded')",
        ),
    )

    op.create_table(
        'scheduled_job_run',
        sa.Column('run_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('job', sa.String(), nullable=False),
        sa.Column('occurrence', TZ, nullable=False),
        sa.Column('mode', sa.String(), nullable=False),
        sa.Column('epoch', sa.Integer(), nullable=False),
        sa.Column('pod_uid', sa.String(), nullable=False, index=True),
        sa.Column('process_id', UUID(as_uuid=True), nullable=False),
        sa.Column('child_pid', sa.Integer(), nullable=True),
        sa.Column('started_at', TZ, nullable=False, server_default=NOW),
        sa.Column('child_exited_at', TZ, nullable=True),
        sa.Column('finished_at', TZ, nullable=True),
        sa.Column('outcome', sa.String(), nullable=True),
        sa.Column('items_claimed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('items_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('progress', JSONB(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['job', 'occurrence'],
            ['scheduled_job_occurrence.job', 'scheduled_job_occurrence.occurrence'],
            name='fk_scheduled_job_run_occurrence',
        ),
        _check('ck_scheduled_job_run_mode', f'mode IN {MODES}'),
    )
    op.create_index(
        'ix_scheduled_job_run_job_occurrence',
        'scheduled_job_run',
        ['job', 'occurrence'],
    )

    op.create_table(
        'scheduled_job_target_visit',
        sa.Column('target_kind', sa.String(), primary_key=True),
        sa.Column('target_id', sa.String(), primary_key=True),
        sa.Column('last_visited_at', TZ, nullable=False),
    )

    # No foreign key to gitlab_webhook: a row can be deleted by today's code
    # paths, and its intents must not block that.
    op.create_table(
        'gitlab_hook_intent',
        sa.Column('uuid', UUID(as_uuid=True), primary_key=True),
        sa.Column('webhook_id', sa.Integer(), nullable=False, index=True),
        sa.Column('run_id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', TZ, nullable=False, server_default=NOW),
        sa.Column('state', sa.String(), nullable=False, server_default='open'),
        _check(
            'ck_gitlab_hook_intent_state',
            "state IN ('open', 'committed', 'abandoned')",
        ),
    )

    op.create_table(
        'gitlab_delivery',
        sa.Column('key', sa.String(), primary_key=True),
        sa.Column('received_at', TZ, nullable=False, server_default=NOW, index=True),
    )

    op.create_table(
        'gitlab_dedupe_replica',
        sa.Column('process_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('pod_uid', sa.String(), nullable=False),
        sa.Column('deployment', sa.String(), nullable=False),
        sa.Column('last_seen', TZ, nullable=False, server_default=NOW),
    )

    op.create_table(
        'scheduled_job_bootstrap',
        sa.Column('id', sa.SmallInteger(), primary_key=True, server_default='1'),
        sa.Column('state', sa.String(), nullable=False, server_default='pending'),
        sa.Column('fenced_at', TZ, nullable=True),
        sa.Column('opened_at', TZ, nullable=True),
        sa.Column('aborted_at', TZ, nullable=True),
        sa.Column('aborted_by', sa.String(), nullable=True),
        sa.Column(
            'legacy_enrich_observed', sa.Integer(), nullable=False, server_default='0'
        ),
        sa.Column(
            'pre_r0_senders_observed', sa.Integer(), nullable=False, server_default='0'
        ),
        sa.Column(
            'pre_r0_legacy_observed', sa.Integer(), nullable=False, server_default='0'
        ),
        sa.Column(
            'legacy_exposure', sa.String(), nullable=False, server_default='unknown'
        ),
        sa.Column('gitlab_handover_at', TZ, nullable=True),
        sa.Column(
            'gitlab_handover_gen', sa.BigInteger(), nullable=False, server_default='0'
        ),
        _check('ck_scheduled_job_bootstrap_single_row', 'id = 1'),
        _check(
            'ck_scheduled_job_bootstrap_state',
            "state IN ('pending', 'fenced', 'active', 'aborted')",
        ),
        _check(
            'ck_scheduled_job_bootstrap_legacy_exposure',
            "legacy_exposure = 'unknown'",
        ),
    )
    op.execute('INSERT INTO scheduled_job_bootstrap (id) VALUES (1)')

    op.create_table(
        'gitlab_handover_attempt',
        sa.Column('gen', sa.BigInteger(), primary_key=True),
        sa.Column('committed_at', TZ, nullable=False),
        sa.Column('cutoff_t', TZ, nullable=True),
        sa.Column('writer_drained_at', TZ, nullable=True),
        sa.Column('swept_at', TZ, nullable=True),
        sa.Column('swept_rows', sa.Integer(), nullable=True),
        sa.Column('cutoff_t2', TZ, nullable=True),
        sa.Column('reader_drained_at', TZ, nullable=True),
        sa.Column('outcome', sa.String(), nullable=False, server_default='open'),
        _check(
            'ck_gitlab_handover_attempt_outcome',
            "outcome IN ('open', 'completed', 'invalidated')",
        ),
    )
    op.create_index(
        'uq_gitlab_handover_attempt_one_open',
        'gitlab_handover_attempt',
        ['outcome'],
        unique=True,
        postgresql_where=sa.text("outcome = 'open'"),
    )

    op.create_table(
        'scheduled_job_pre_r0_pod',
        sa.Column('pod_uid', sa.String(), primary_key=True),
        sa.Column('pod_name', sa.String(), nullable=False),
        sa.Column('job', sa.String(), nullable=False),
        sa.Column('first_seen', TZ, nullable=False, server_default=NOW),
        sa.Column('resolution', sa.String(), nullable=True),
        sa.Column('resolved_by', sa.String(), nullable=True),
        sa.Column('resolved_at', TZ, nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        _check(
            'ck_scheduled_job_pre_r0_pod_resolution',
            "resolution IN ('e4', 'accepted')",
        ),
    )

    for table in CLAIMED_TABLES:
        op.add_column(table, sa.Column('claim_run_id', UUID(as_uuid=True)))
        op.add_column(table, sa.Column('claimed_at', TZ))

    op.add_column('gitlab_webhook', sa.Column('webhook_secret_v2', sa.String()))

    op.add_column('resend_synced_users', sa.Column('welcome_email_status', sa.String()))
    op.add_column('resend_synced_users', sa.Column('welcome_email_payload', JSONB()))
    op.add_column(
        'resend_synced_users', sa.Column('welcome_email_first_attempt_at', TZ)
    )
    op.add_column('resend_synced_users', sa.Column('welcome_email_next_attempt_at', TZ))
    op.add_column('resend_synced_users', sa.Column('welcome_email_id', sa.String()))
    op.create_check_constraint(
        'ck_resend_synced_users_welcome_email_status',
        'resend_synced_users',
        "welcome_email_status IN ('PENDING', 'SENT', 'UNRESOLVED', 'FAILED')",
    )
    op.create_index(
        'ix_resend_synced_users_welcome_email_pending',
        'resend_synced_users',
        ['welcome_email_next_attempt_at'],
        postgresql_where=sa.text("welcome_email_status = 'PENDING'"),
    )

    for statement in TRIGGERS:
        op.execute(statement)
    for table in APPEND_ONLY_TABLES:
        op.execute(f'REVOKE DELETE, TRUNCATE ON {table} FROM PUBLIC, CURRENT_USER')


def downgrade() -> None:
    op.execute('DROP TRIGGER gitlab_webhook_secret_moved ON gitlab_webhook')
    op.execute('DROP FUNCTION gitlab_webhook_secret_moved()')

    op.drop_index(
        'ix_resend_synced_users_welcome_email_pending', table_name='resend_synced_users'
    )
    op.drop_constraint(
        'ck_resend_synced_users_welcome_email_status', 'resend_synced_users'
    )
    for column in (
        'welcome_email_id',
        'welcome_email_next_attempt_at',
        'welcome_email_first_attempt_at',
        'welcome_email_payload',
        'welcome_email_status',
    ):
        op.drop_column('resend_synced_users', column)
    op.drop_column('gitlab_webhook', 'webhook_secret_v2')
    for table in CLAIMED_TABLES:
        op.drop_column(table, 'claimed_at')
        op.drop_column(table, 'claim_run_id')

    for table in (
        'scheduled_job_pre_r0_pod',
        'gitlab_handover_attempt',
        'scheduled_job_bootstrap',
        'gitlab_dedupe_replica',
        'gitlab_delivery',
        'gitlab_hook_intent',
        'scheduled_job_target_visit',
        'scheduled_job_run',
        'scheduled_job_occurrence',
        'scheduled_job_observation',
        'scheduled_job_process',
        'scheduled_job_pod',
        'scheduled_job_permit',
    ):
        op.drop_table(table)
    op.execute('DROP FUNCTION scheduled_job_pre_r0_pod_resolution_final()')
    op.execute('DROP FUNCTION scheduled_job_pod_permanent()')
