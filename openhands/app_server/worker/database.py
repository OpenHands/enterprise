"""The worker's connection to the app's database.

procrastinate reaches Postgres through psycopg, while the rest of the app uses
asyncpg and pg8000. The worker builds its psycopg connections from the app's
own database settings, so it reaches the same database the same way: directly
at ``DB_HOST``, or through the Cloud SQL Python Connector when
``GCP_DB_INSTANCE`` is set.
"""

from dataclasses import dataclass

from procrastinate import PsycopgConnector

from openhands.app_server.services.db_session_injector import DbSessionInjector
from openhands.app_server.worker.cloud_sql import CloudSqlDatabase
from openhands.db.ssl import build_psycopg_connect_args


@dataclass
class WorkerDatabase:
    connector: PsycopgConnector
    cloud_sql: CloudSqlDatabase | None = None

    @classmethod
    def from_settings(cls, db: DbSessionInjector, pool_size: int) -> 'WorkerDatabase':
        password = db.password.get_secret_value() if db.password else None
        if db.gcp_db_instance:
            if not (db.user and password and db.name):
                raise RuntimeError(
                    'Cloud SQL needs DB_USER, DB_PASS and DB_NAME alongside '
                    'GCP_DB_INSTANCE.'
                )
            cloud_sql = CloudSqlDatabase(
                f'{db.gcp_project}:{db.gcp_region}:{db.gcp_db_instance}',
                user=db.user,
                password=password,
                db=db.name,
            )
            connector = PsycopgConnector(
                connection_class=cloud_sql.connection_class(),
                min_size=1,
                max_size=pool_size,
            )
            return cls(connector=connector, cloud_sql=cloud_sql)

        if not db.host:
            raise RuntimeError(
                'No database configured. Set DB_HOST (or GCP_DB_INSTANCE for Cloud '
                'SQL) to point at a PostgreSQL server.'
            )
        connector = PsycopgConnector(
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
        return cls(connector=connector)

    async def close(self) -> None:
        if self.cloud_sql is not None:
            await self.cloud_sql.close()
