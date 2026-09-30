"""Task registration, role scoping and catch-up, without a database."""

from __future__ import annotations

import inspect
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from procrastinate.periodic import PeriodicDeferrer

from server.task_queue.app import build_app, longest_gap
from server.task_queue.config import (
    DEFAULT_MAX_DELAY,
    Role,
    Settings,
    conninfo_from_env,
)
from server.task_queue.jobs import JOBS, JOBS_BY_NAME

ALL_JOBS = frozenset(job.name for job in JOBS)
BASE = Settings(
    role=Role.BUSINESS,
    conninfo='host=unused',
    scheduling_enabled=True,
    enabled_jobs=ALL_JOBS,
)


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp()


def _deferred(
    settings: Settings, at: float, max_delay: float | None = None
) -> dict[str, datetime]:
    app = build_app(settings)
    defaults = dict(app.periodic_defaults)
    if max_delay is not None:
        defaults['max_delay'] = max_delay
    deferrer = PeriodicDeferrer(registry=app.periodic_registry, **defaults)
    return {
        periodic_task.periodic_id: datetime.fromtimestamp(timestamp, UTC)
        for periodic_task, timestamp in deferrer.get_previous_tasks(at=at)
    }


class TestTaskDefinitions:
    def test_every_task_has_an_execution_lock_and_no_queueing_lock(self):
        app = build_app(BASE)
        for job in JOBS:
            task = app.tasks[job.task_name]
            assert task.queueing_lock is None
            assert task.lock == job.task_name
            assert task.queue == job.role.queue

    def test_locks_are_unique_per_job(self):
        assert len({job.lock for job in JOBS}) == len(JOBS)

    @pytest.mark.parametrize('role', list(Role))
    def test_every_role_defines_every_task(self, role):
        app = build_app(replace(BASE, role=role, scheduling_enabled=False))
        assert {job.task_name for job in JOBS} <= set(app.tasks)


@pytest.mark.parametrize('job', JOBS, ids=lambda job: job.name)
def test_each_job_runs_an_existing_async_main(job):
    # Enrichment builds its GitHub App client at import; nothing reaches GitHub.
    with patch('integrations.github.data_collector.GitHubDataCollector'):
        main = job.load()
    assert inspect.iscoroutinefunction(main)
    assert main.__module__ == job.module


class TestPeriodicRegistration:
    def test_business_role_schedules_business_jobs(self):
        app = build_app(BASE)
        assert {pid for _, pid in app.periodic_registry.periodic_tasks} == ALL_JOBS

    def test_ops_role_schedules_no_business_job(self):
        app = build_app(replace(BASE, role=Role.OPS))
        assert app.periodic_registry.periodic_tasks == {}

    def test_scheduling_switch_off_keeps_tasks_but_schedules_nothing(self):
        app = build_app(replace(BASE, scheduling_enabled=False))
        assert app.periodic_registry.periodic_tasks == {}
        assert JOBS[0].task_name in app.tasks

    def test_only_enabled_jobs_are_scheduled(self):
        app = build_app(
            replace(BASE, enabled_jobs=frozenset({'enrich_user_interaction_data'}))
        )
        assert [pid for _, pid in app.periodic_registry.periodic_tasks] == [
            'enrich_user_interaction_data'
        ]

    def test_schedule_override(self):
        app = build_app(
            replace(BASE, cron_overrides={'clean_proactive_convo_table': '0 5 * * *'})
        )
        task = app.periodic_registry.periodic_tasks[
            (
                JOBS_BY_NAME['clean_proactive_convo_table'].task_name,
                'clean_proactive_convo_table',
            )
        ]
        assert task.cron == '0 5 * * *'

    def test_rejects_invalid_schedule(self):
        with pytest.raises(ValueError, match='invalid cron'):
            build_app(
                replace(BASE, cron_overrides={'clean_proactive_convo_table': 'nope'})
            )

    def test_rejects_schedule_longer_than_the_catch_up_window(self):
        with pytest.raises(ValueError, match='max_delay'):
            build_app(
                replace(
                    BASE, cron_overrides={'clean_proactive_convo_table': '0 2 * * 0'}
                )
            )

    def test_longest_gap(self):
        assert longest_gap('*/10 * * * *') == timedelta(minutes=10)
        assert longest_gap('0 3 * * *') == timedelta(days=1)
        assert longest_gap('0 0 1 * *') == timedelta(days=31)


class TestCatchUp:
    def test_catch_up_window_covers_a_daily_schedule(self):
        max_delay = build_app(BASE).periodic_defaults['max_delay']
        assert max_delay == DEFAULT_MAX_DELAY.total_seconds()
        assert DEFAULT_MAX_DELAY > timedelta(days=1)

    def test_daily_job_started_hours_late_is_deferred_not_dropped(self):
        deferred = _deferred(BASE, at=_ts('2026-03-10T23:59:00'))
        assert deferred['clean_app_conversation_start_tasks'] == datetime(
            2026, 3, 10, 3, tzinfo=UTC
        )

    def test_missed_window_matches_configured_max_delay(self):
        """Procrastinate's default 600 s window would silently drop this run."""
        at = _ts('2026-03-10T05:00:00')
        assert 'clean_app_conversation_start_tasks' not in _deferred(
            BASE, at, max_delay=600
        )
        assert 'clean_app_conversation_start_tasks' in _deferred(BASE, at)

    def test_schedules_are_utc(self, monkeypatch):
        monkeypatch.setenv('TZ', 'America/New_York')
        time.tzset()
        try:
            deferred = _deferred(BASE, at=_ts('2026-07-01T04:00:00'))
        finally:
            monkeypatch.delenv('TZ')
            time.tzset()
        assert deferred['clean_app_conversation_start_tasks'] == datetime(
            2026, 7, 1, 3, tzinfo=UTC
        )
        assert deferred['clean_proactive_convo_table'] == datetime(
            2026, 7, 1, 2, tzinfo=UTC
        )


class TestSettingsFromEnv:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith(('TASK_QUEUE_', 'DB_', 'GCP_')):
                monkeypatch.delenv(key)
        monkeypatch.setenv('DB_HOST', 'db.internal')
        monkeypatch.setenv('TASK_QUEUE_ROLE', 'business')

    def test_defaults_schedule_nothing(self):
        settings = Settings.from_env()
        assert settings.role is Role.BUSINESS
        assert settings.scheduling_enabled is False
        assert settings.enabled_jobs == frozenset()
        assert settings.max_delay == DEFAULT_MAX_DELAY

    def test_flags_and_overrides(self, monkeypatch):
        monkeypatch.setenv('TASK_QUEUE_SCHEDULING_ENABLED', 'true')
        monkeypatch.setenv('TASK_QUEUE_ENRICH_USER_INTERACTION_DATA_ENABLED', '1')
        monkeypatch.setenv(
            'TASK_QUEUE_ENRICH_USER_INTERACTION_DATA_SCHEDULE', '*/5 * * * *'
        )
        settings = Settings.from_env()
        assert settings.scheduling_enabled is True
        assert settings.enabled_jobs == {'enrich_user_interaction_data'}
        assert settings.cron_overrides == {
            'enrich_user_interaction_data': '*/5 * * * *'
        }

    def test_unknown_role_is_refused(self, monkeypatch):
        monkeypatch.setenv('TASK_QUEUE_ROLE', 'both')
        with pytest.raises(ValueError):
            Settings.from_env()

    def test_cloud_sql_connector_is_refused(self, monkeypatch):
        monkeypatch.setenv('GCP_DB_INSTANCE', 'instance')
        with pytest.raises(RuntimeError, match='Cloud SQL'):
            conninfo_from_env()

    def test_conninfo_carries_ssl_mode_and_keepalives(self, monkeypatch):
        monkeypatch.setenv('DB_SSL_MODE', 'require')
        conninfo = conninfo_from_env()
        assert 'sslmode=require' in conninfo
        assert 'keepalives=1' in conninfo
        assert 'host=db.internal' in conninfo
