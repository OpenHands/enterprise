"""Scheduling handoff watermarks: which occurrences the CronJobs already ran.

A single cutover instant loses runs. Suspend at 05:59, a daily job due at 06:00,
drain done at 06:02: a cutover timestamp of 06:02 skips 06:00 though nothing
ran it. Instead the operator records, per job, the last occurrence known to
have completed, and each occurrence decides against that:

========== ================================================
completed  at or before the watermark: skip
skipped    at or before the watermark: skip, deliberately
missed     run
unknown    run - a duplicate is bounded, a skip is silent loss
========== ================================================

Anything after the watermark runs. The watermark is advisory: a crash between
a run and its advancement leaves it stale and the occurrence runs again, which
the jobs' idempotency makes safe. The safe direction is always to re-run.

Usage at cutover, after the CronJobs are suspended and drained::

    python -m server.task_queue.watermark record \\
        --job enrich_user_interaction_data \\
        --occurrence 2026-09-30T06:00:00+00:00 --classification completed \\
        --by alice
    python -m server.task_queue.watermark show
"""

from __future__ import annotations

import argparse
import enum
import sys
from dataclasses import dataclass
from datetime import datetime

import psycopg
from procrastinate import BaseConnector
from psycopg.rows import dict_row

from server.task_queue.config import conninfo_from_env
from server.task_queue.jobs import JOBS_BY_NAME, ScheduledJob


class Classification(str, enum.Enum):
    COMPLETED = 'completed'
    MISSED = 'missed'
    SKIPPED = 'skipped'
    UNKNOWN = 'unknown'


SKIP_AT_OR_BEFORE = frozenset({Classification.COMPLETED, Classification.SKIPPED})

_SELECT = """
SELECT job_name, occurrence, classification, supersedes_earlier, source,
       recorded_by, recorded_at
  FROM scheduled_job_watermark
"""

_ADVANCE = """
INSERT INTO scheduled_job_watermark
       (job_name, occurrence, classification, supersedes_earlier, source)
VALUES (%(job_name)s, %(occurrence)s, 'completed', %(supersedes)s, 'task')
ON CONFLICT (job_name) DO UPDATE
   SET occurrence = EXCLUDED.occurrence,
       classification = 'completed',
       supersedes_earlier = EXCLUDED.supersedes_earlier,
       source = 'task',
       recorded_by = NULL,
       recorded_at = now()
 WHERE scheduled_job_watermark.occurrence < EXCLUDED.occurrence
    OR (scheduled_job_watermark.occurrence = EXCLUDED.occurrence
        AND scheduled_job_watermark.classification <> 'completed')
"""

_RECORD = """
INSERT INTO scheduled_job_watermark
       (job_name, occurrence, classification, supersedes_earlier, source,
        recorded_by)
VALUES (%(job_name)s, %(occurrence)s, %(classification)s, %(supersedes)s,
        'operator', %(recorded_by)s)
ON CONFLICT (job_name) DO UPDATE
   SET occurrence = EXCLUDED.occurrence,
       classification = EXCLUDED.classification,
       supersedes_earlier = EXCLUDED.supersedes_earlier,
       source = 'operator',
       recorded_by = EXCLUDED.recorded_by,
       recorded_at = now()
"""


@dataclass(frozen=True)
class Watermark:
    job_name: str
    occurrence: datetime
    classification: Classification
    supersedes_earlier: bool

    def covers(self, job: ScheduledJob, occurrence: datetime) -> bool:
        """Whether ``occurrence`` is already accounted for and must not run."""
        return (
            job.single_watermark
            and self.supersedes_earlier
            and self.classification in SKIP_AT_OR_BEFORE
            and occurrence <= self.occurrence
        )


async def load(connector: BaseConnector, job: ScheduledJob) -> Watermark | None:
    rows = await connector.execute_query_all_async(
        _SELECT + ' WHERE job_name = %(job_name)s', job_name=job.name
    )
    if not rows:
        return None
    [row] = rows
    return Watermark(
        job_name=row['job_name'],
        occurrence=row['occurrence'],
        classification=Classification(row['classification']),
        supersedes_earlier=row['supersedes_earlier'],
    )


async def advance(
    connector: BaseConnector, job: ScheduledJob, occurrence: datetime
) -> None:
    """Move the watermark forward to an occurrence that just ran. Never back."""
    await connector.execute_query_async(
        _ADVANCE,
        job_name=job.name,
        occurrence=occurrence,
        supersedes=job.single_watermark,
    )


def record(
    conninfo: str,
    job: ScheduledJob,
    occurrence: datetime,
    classification: Classification,
    recorded_by: str,
) -> None:
    """Operator record at cutover. Authoritative: it may move the mark back."""
    if occurrence.tzinfo is None:
        raise ValueError('occurrence must carry a UTC offset')
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(
            _RECORD,
            {
                'job_name': job.name,
                'occurrence': occurrence,
                'classification': classification.value,
                'supersedes': job.single_watermark,
                'recorded_by': recorded_by,
            },
        )


def show(conninfo: str) -> list[dict]:
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        return conn.execute(_SELECT + ' ORDER BY job_name').fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m server.task_queue.watermark')
    commands = parser.add_subparsers(dest='command', required=True)
    rec = commands.add_parser('record', help='record a job watermark at cutover')
    rec.add_argument('--job', required=True, choices=sorted(JOBS_BY_NAME))
    rec.add_argument(
        '--occurrence',
        required=True,
        type=datetime.fromisoformat,
        help='ISO 8601 with offset, e.g. 2026-09-30T06:00:00+00:00',
    )
    rec.add_argument(
        '--classification', required=True, choices=[c.value for c in Classification]
    )
    rec.add_argument('--by', required=True, help='who is recording this')
    commands.add_parser('show', help='print every recorded watermark')
    args = parser.parse_args(argv)

    conninfo = conninfo_from_env()
    if args.command == 'record':
        try:
            record(
                conninfo,
                JOBS_BY_NAME[args.job],
                args.occurrence,
                Classification(args.classification),
                args.by,
            )
        except ValueError as error:
            parser.error(str(error))
        return 0
    for row in show(conninfo):
        print(
            f'{row["job_name"]}\t{row["occurrence"].isoformat()}\t'
            f'{row["classification"]}\tsupersedes={row["supersedes_earlier"]}\t'
            f'{row["source"]}\t{row["recorded_by"] or "-"}\t'
            f'{row["recorded_at"].isoformat()}'
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
