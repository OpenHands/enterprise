"""The worker's procrastinate app, and its settings."""

from procrastinate import App, PsycopgConnector
from pydantic import BaseModel, Field

from openhands.app_server.worker.housekeeping import housekeeping


class WorkerConfig(BaseModel):
    """Worker settings, from ``OH_WORKER_*`` environment variables."""

    concurrency: int = Field(
        default=10, description='How many jobs the worker runs at once.'
    )


# procrastinate binds each task to the one app its blueprint is added to, so
# there is one app per process. It is given its database connection when it
# runs, with ``app.replace_connector``; until then its connector is never
# opened.
#
# Every task has an explicit name, so that moving its code does not strand jobs
# already in the queue.
app = App(connector=PsycopgConnector())
app.add_tasks_from(housekeeping, namespace='worker')
