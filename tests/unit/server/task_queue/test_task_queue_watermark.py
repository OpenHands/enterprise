"""Scheduling handoff: watermarks decide which occurrences still run."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from procrastinate import App
from procrastinate.periodic import PeriodicDeferrer
from psycopg.rows import dict_row

from server.task_queue import watermark
from server.task_queue.app import build_app
from server.task_queue.jobs import JOBS_BY_NAME
from server.task_queue.watermark import Classification, Watermark
from server.task_queue.worker import build_worker

CLEAN = JOBS_BY_NAME['clean_proactive_convo_table']  # daily at 02:00
DUE = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)
PREVIOUS = DUE - timedelta(days=1)


class TestCovers:
    @pytest.mark.parametrize(
        ('classification', 'at_or_before', 'after'),
        [
            (Classification.COMPLETED, True, False),
            (Classification.SKIPPED, True, False),
            (Classification.MISSED, False, False),
            (Classification.UNKNOWN, False, False),
        ],
    )
    def test_decision_table(self, classification, at_or_before, after):
        mark = Watermark(CLEAN.name, DUE, classification, supersedes_earlier=True)
        assert mark.covers(CLEAN, DUE - timedelta(days=1)) is at_or_before
        assert mark.covers(CLEAN, DUE) is at_or_before
        assert mark.covers(CLEAN, DUE + timedelta(days=1)) is after

    def test_no_skip_without_the_supersession_assumption(self):
        mark = Watermark(CLEAN.name, DUE, Classification.COMPLETED, False)
        assert not mark.covers(CLEAN, DUE)
        occurrence_specific = replace(CLEAN, single_watermark=False)
        mark = Watermark(CLEAN.name, DUE, Classification.COMPLETED, True)
        assert not mark.covers(occurrence_specific, DUE)


def _mark(conninfo: str) -> dict | None:
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        return conn.execute(
            'SELECT occurrence, classification, source FROM scheduled_job_watermark '
            'WHERE job_name = %s',
            (CLEAN.name,),
        ).fetchone()


async def _enable_at(app: App, settings, at: datetime) -> None:
    """Scheduling switched on at ``at``: one deferrer round, then drain."""
    deferrer = PeriodicDeferrer(registry=app.periodic_registry, **app.periodic_defaults)
    await deferrer.defer_jobs(deferrer.get_previous_tasks(at=at.timestamp()))
    worker = build_worker(settings, app)
    worker.wait = False
    await worker.run()


@pytest.fixture
def handoff(make_settings, job_bodies):
    runs: list[str] = []

    async def body() -> None:
        runs.append(CLEAN.name)

    job_bodies[CLEAN.name] = body
    settings = make_settings(enabled_jobs=frozenset({CLEAN.name}))

    async def enable_at(at: datetime) -> list[str]:
        app = build_app(settings)
        async with app.open_async():
            await _enable_at(app, settings, at)
        return runs

    return enable_at


class TestHandoff:
    async def test_occurrence_due_during_the_drain_runs(self, conninfo, handoff):
        """Suspend 01:59, due 02:00, drain done 02:01: 02:00 must still run."""
        watermark.record(
            conninfo, CLEAN, PREVIOUS, Classification.COMPLETED, 'operator'
        )
        assert await handoff(DUE + timedelta(minutes=1)) == [CLEAN.name]
        assert _mark(conninfo) == {
            'occurrence': DUE,
            'classification': 'completed',
            'source': 'task',
        }

    async def test_occurrence_the_cronjob_already_ran_is_skipped(
        self, conninfo, handoff
    ):
        watermark.record(conninfo, CLEAN, DUE, Classification.COMPLETED, 'operator')
        assert await handoff(DUE + timedelta(minutes=1)) == []
        assert _mark(conninfo)['source'] == 'operator'

    async def test_unknown_runs_rather_than_skips(self, conninfo, handoff):
        watermark.record(conninfo, CLEAN, DUE, Classification.UNKNOWN, 'operator')
        assert await handoff(DUE + timedelta(minutes=1)) == [CLEAN.name]
        assert _mark(conninfo)['classification'] == 'completed'

    async def test_deliberately_skipped_occurrence_is_not_rerun(
        self, conninfo, handoff
    ):
        watermark.record(conninfo, CLEAN, DUE, Classification.SKIPPED, 'operator')
        assert await handoff(DUE + timedelta(minutes=1)) == []

    async def test_no_watermark_runs(self, conninfo, handoff):
        assert await handoff(DUE + timedelta(minutes=1)) == [CLEAN.name]

    @pytest.mark.parametrize(
        ('recorded', 'runs'), [(PREVIOUS, [CLEAN.name]), (DUE, [])]
    )
    async def test_daily_job_restarted_hours_late_is_deferred_then_decided(
        self, conninfo, handoff, recorded, runs
    ):
        """Not dropped by max_delay; the watermark makes the call."""
        watermark.record(conninfo, CLEAN, recorded, Classification.COMPLETED, 'op')
        assert await handoff(DUE + timedelta(hours=10)) == runs
        with psycopg.connect(conninfo) as conn:
            deferred = conn.execute(
                'SELECT count(*) FROM procrastinate_jobs WHERE task_name = %s',
                (CLEAN.task_name,),
            ).fetchone()[0]
        assert deferred == 1


class TestAdvance:
    async def test_never_moves_the_watermark_back(self, conninfo, make_settings):
        later = DUE + timedelta(days=1)
        watermark.record(conninfo, CLEAN, later, Classification.COMPLETED, 'op')
        app = build_app(make_settings())
        async with app.open_async():
            await watermark.advance(app.connector, CLEAN, DUE)
        assert _mark(conninfo)['occurrence'] == later

    async def test_failed_advance_does_not_fail_the_run(
        self, conninfo, handoff, monkeypatch
    ):
        async def broken(*args, **kwargs):
            raise RuntimeError('database went away')

        monkeypatch.setattr(watermark, 'advance', broken)
        assert await handoff(DUE + timedelta(minutes=1)) == [CLEAN.name]
        with psycopg.connect(conninfo) as conn:
            status = conn.execute(
                'SELECT status::text FROM procrastinate_jobs WHERE task_name = %s',
                (CLEAN.task_name,),
            ).fetchone()[0]
        assert status == 'succeeded'
        assert _mark(conninfo) is None


class TestCli:
    @pytest.fixture
    def db_env(self, monkeypatch, test_database):
        server = test_database.server
        for key, value in {
            'DB_HOST': server.host,
            'DB_PORT': str(server.port),
            'DB_NAME': test_database.name,
            'DB_USER': server.user,
            'DB_PASS': server.password,
        }.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv('GCP_DB_INSTANCE', raising=False)

    def test_record_then_show(self, db_env, conninfo, capsys):
        assert (
            watermark.main(
                [
                    'record',
                    '--job',
                    CLEAN.name,
                    '--occurrence',
                    DUE.isoformat(),
                    '--classification',
                    'completed',
                    '--by',
                    'alice',
                ]
            )
            == 0
        )
        assert watermark.main(['show']) == 0
        line = capsys.readouterr().out.strip()
        assert line.startswith(f'{CLEAN.name}\t{DUE.isoformat()}\tcompleted\t')
        assert '\toperator\talice\t' in line

    def test_rejects_an_occurrence_without_offset(self, db_env):
        with pytest.raises(SystemExit) as exit_info:
            watermark.main(
                [
                    'record',
                    '--job',
                    CLEAN.name,
                    '--occurrence',
                    '2026-09-30T02:00:00',
                    '--classification',
                    'completed',
                    '--by',
                    'alice',
                ]
            )
        assert exit_info.value.code == 2
