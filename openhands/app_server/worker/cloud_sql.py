"""Cloud SQL connections for the worker's psycopg pool.

procrastinate needs async psycopg connections. The Cloud SQL Python Connector
opens psycopg connections through its TLS tunnel, but only sync ones. Both
kinds wrap the same libpq connection object, so the async connection here wraps
the libpq connection of a sync one the connector opened. psycopg's own
``AsyncConnection.connect`` builds its connections from a libpq connection the
same way.
"""

import asyncio
from typing import Any, Self

import psycopg
from google.cloud.sql.connector import Connector


class CloudSqlDatabase:
    """One Cloud SQL database, reached through the Cloud SQL Python Connector."""

    def __init__(
        self, instance_connection_name: str, user: str, password: str, db: str
    ):
        self.instance_connection_name = instance_connection_name
        self.user = user
        self.password = password
        self.db = db
        self._connector: Connector | None = None

    async def open_sync_connection(self) -> psycopg.Connection:
        """Open a sync psycopg connection through the connector's tunnel."""
        if self._connector is None:
            # The connector refreshes its certificates on the loop it is given.
            self._connector = Connector(loop=asyncio.get_running_loop())
        return await self._connector.connect_async(
            self.instance_connection_name,
            'psycopg',
            user=self.user,
            password=self.password,
            db=self.db,
        )

    async def close(self) -> None:
        """Stop the connector's certificate refreshes."""
        if self._connector is not None:
            await self._connector.close_async()
            self._connector = None

    def connection_class(self) -> type[psycopg.AsyncConnection]:
        """An ``AsyncConnection`` class that connects to this database.

        psycopg_pool opens every connection with ``connection_class.connect``,
        and procrastinate opens its LISTEN connection the same way, so this one
        class covers every connection the worker makes.
        """
        database = self

        class CloudSqlAsyncConnection(psycopg.AsyncConnection):
            # The sync connection shares this one's libpq connection. It is
            # kept for as long as this one, because collecting it while the
            # libpq connection is open raises a ResourceWarning.
            _sync_connection: psycopg.Connection

            @classmethod
            async def connect(
                cls,
                conninfo: str = '',
                *,
                autocommit: bool = False,
                prepare_threshold: int | None = 5,
                row_factory: Any = None,
                **kwargs: Any,
            ) -> Self:
                # The connector decides how to reach the instance, so conninfo
                # and the pool's connection options have nothing to set.
                sync_connection = await database.open_sync_connection()
                connection = cls(sync_connection.pgconn)
                connection._sync_connection = sync_connection
                connection.prepare_threshold = prepare_threshold
                if row_factory is not None:
                    connection.row_factory = row_factory
                if autocommit:
                    await connection.set_autocommit(True)
                return connection

        return CloudSqlAsyncConnection
