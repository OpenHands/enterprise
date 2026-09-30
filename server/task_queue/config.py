"""Environment-derived settings for the task queue worker.

Everything is read once into a frozen ``Settings``, so tests build settings
directly instead of patching the environment.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from datetime import timedelta

from psycopg.conninfo import make_conninfo

from openhands.db.ssl import normalize_db_ssl_mode


class Role(str, enum.Enum):
    """Which periodic registrations a worker attaches, and which queue it consumes.

    Every worker defines every task; only the matching role schedules them.
    Procrastinate's periodic deferrer reads the whole periodic registry and
    ignores which queues the worker consumes, so without this an ops worker
    would schedule business jobs.
    """

    BUSINESS = 'business'
    OPS = 'ops'

    @property
    def queue(self) -> str:
        return self.value


# Every attempt a Class A job gets, first run included. Procrastinate counts
# retries, so its RetryStrategy is configured with MAX_ATTEMPTS - 1.
MAX_ATTEMPTS = 3

# A discarded occurrence cannot be recovered downstream, so the deferrer's
# catch-up window has to exceed the longest schedule interval in scope (daily).
DEFAULT_MAX_DELAY = timedelta(hours=26)


def env_flag(name: str, default: str = '0') -> bool:
    return os.getenv(name, default).strip().lower() in ('1', 'true')


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f'{name} must be positive, got {value}')
    return value


def conninfo_from_env() -> str:
    """libpq connection string for the worker's psycopg pool.

    Uses the app's ``DB_*`` variables. The Cloud SQL connector path
    (``GCP_DB_INSTANCE``) hands SQLAlchemy a connection factory, which a psycopg
    pool cannot use, so it is refused rather than silently bypassed.
    """
    if os.getenv('GCP_DB_INSTANCE'):
        raise RuntimeError(
            'The task queue worker does not support the Cloud SQL connector '
            '(GCP_DB_INSTANCE). Configure DB_HOST instead.'
        )
    host = os.getenv('DB_HOST')
    if not host:
        raise RuntimeError('No database configured. Set DB_HOST.')

    params: dict[str, str | int] = dict(
        host=host,
        port=int(os.getenv('DB_PORT', '5432')),
        dbname=os.getenv('DB_NAME', 'openhands'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASS', 'postgres'),
        application_name=f'task-queue-{os.getenv("TASK_QUEUE_ROLE", "worker")}',
        connect_timeout=10,
        # Detect a dead peer on an established socket: a server-side timeout
        # cannot unblock a client read on a black-holed connection.
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        tcp_user_timeout=60_000,
    )
    ssl_mode = normalize_db_ssl_mode(os.getenv('DB_SSL_MODE') or os.getenv('PGSSLMODE'))
    if ssl_mode:
        params['sslmode'] = ssl_mode
    return make_conninfo(**params)


@dataclass(frozen=True)
class Settings:
    role: Role
    conninfo: str = field(repr=False)
    scheduling_enabled: bool = False
    enabled_jobs: frozenset[str] = frozenset()
    cron_overrides: dict[str, str] = field(default_factory=dict)
    max_delay: timedelta = DEFAULT_MAX_DELAY
    concurrency: int = 1
    update_heartbeat_interval: float = 10.0
    stalled_worker_timeout: float = 30.0
    fetch_job_polling_interval: float = 5.0
    pool_timeout: float = 30.0
    listen_notify: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        from server.task_queue.jobs import JOBS

        return cls(
            role=Role(os.environ['TASK_QUEUE_ROLE'].strip().lower()),
            conninfo=conninfo_from_env(),
            scheduling_enabled=env_flag('TASK_QUEUE_SCHEDULING_ENABLED'),
            enabled_jobs=frozenset(
                job.name for job in JOBS if env_flag(job.enabled_env)
            ),
            cron_overrides={
                job.name: os.environ[job.schedule_env].strip()
                for job in JOBS
                if os.getenv(job.schedule_env, '').strip()
            },
            max_delay=timedelta(
                seconds=_positive_int(
                    'TASK_QUEUE_MAX_DELAY_SECONDS',
                    int(DEFAULT_MAX_DELAY.total_seconds()),
                )
            ),
            concurrency=_positive_int('TASK_QUEUE_CONCURRENCY', 1),
            listen_notify=env_flag('TASK_QUEUE_LISTEN_NOTIFY', '1'),
        )
