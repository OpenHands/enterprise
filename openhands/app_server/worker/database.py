"""The worker's connection to the app's database.

procrastinate reaches Postgres through psycopg, while the rest of the app uses
asyncpg and pg8000. The worker builds its psycopg connections from the app's
own database settings, and always connects to ``DB_HOST``. On Cloud SQL, that
is a Cloud SQL Auth Proxy running next to the worker.
"""

from procrastinate import PsycopgConnector

from openhands.app_server.services.db_session_injector import DbSessionInjector
from openhands.db.ssl import build_psycopg_connect_args


def build_connector(db: DbSessionInjector, pool_size: int) -> PsycopgConnector:
    if not db.host:
        raise RuntimeError(
            'No database configured. Set DB_HOST to point at a PostgreSQL server, '
            'or at a Cloud SQL Auth Proxy running next to the worker.'
        )
    password = db.password.get_secret_value() if db.password else None
    return PsycopgConnector(
        kwargs={
            'host': db.host,
            'port': db.port,
            'user': db.user,
            'password': password,
            'dbname': db.name,
            **build_psycopg_connect_args(db.ssl_mode),
        },
        min_size=1,
        max_size=pool_size,
    )
