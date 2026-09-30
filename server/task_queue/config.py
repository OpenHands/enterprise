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
    # Per-job time budget, then the grace window G before the watchdog exits
    # the process. The longest observed normal run is about two minutes.
    default_budget: float = 600.0
    budget_overrides: dict[str, float] = field(default_factory=dict)
    watchdog_grace: float = 60.0
    watchdog_interval: float = 5.0
    loop_stall_timeout: float = 60.0
    # 0 disables the liveness endpoint.
    probe_port: int = 0
    recovery_interval: float = 60.0
    recovery_pass_budget: float = 30.0
    retention_hours: int = 24 * 7

    def __post_init__(self) -> None:
        if self.update_heartbeat_interval >= self.stalled_worker_timeout:
            raise ValueError(
                'The heartbeat interval must be shorter than the stalled worker '
                'timeout, or healthy workers look stalled.'
            )

    def budget_for(self, task_name: str) -> float:
        from server.task_queue.jobs import JOBS

        for job in JOBS:
            if job.task_name == task_name:
                return self.budget_overrides.get(job.name, self.default_budget)
        return self.default_budget

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
            default_budget=_positive_int('TASK_QUEUE_DEFAULT_BUDGET_SECONDS', 600),
            budget_overrides={
                job.name: float(_positive_int(job.budget_env, 1))
                for job in JOBS
                if os.getenv(job.budget_env, '').strip()
            },
            watchdog_grace=_positive_int('TASK_QUEUE_WATCHDOG_GRACE_SECONDS', 60),
            watchdog_interval=_positive_int('TASK_QUEUE_WATCHDOG_INTERVAL_SECONDS', 5),
            loop_stall_timeout=_positive_int(
                'TASK_QUEUE_LOOP_STALL_TIMEOUT_SECONDS', 60
            ),
            probe_port=int(os.getenv('TASK_QUEUE_PROBE_PORT', '8080')),
            update_heartbeat_interval=_positive_int(
                'TASK_QUEUE_HEARTBEAT_INTERVAL_SECONDS', 10
            ),
            stalled_worker_timeout=_positive_int(
                'TASK_QUEUE_STALLED_WORKER_TIMEOUT_SECONDS', 30
            ),
            recovery_interval=_positive_int('TASK_QUEUE_RECOVERY_INTERVAL_SECONDS', 60),
            recovery_pass_budget=_positive_int(
                'TASK_QUEUE_RECOVERY_PASS_BUDGET_SECONDS', 30
            ),
            retention_hours=_positive_int('TASK_QUEUE_RETENTION_HOURS', 24 * 7),
        )
