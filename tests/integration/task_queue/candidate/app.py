"""DBOS candidate: a scheduled durable workflow in each FastAPI replica.

Variants (env):
  POC_DBOS_EXECUTOR=default|hostname  default leaves DBOS's executor ID ("local");
                                      hostname sets it per replica, as a StatefulSet would.
  POC_DBOS_PEER_RECOVERY=1            a replica also resumes other executors' stale
                                      PENDING workflows (list_workflows + resume_workflow).
  POC_DBOS_CONDUCTOR_URL/_KEY         connect to a Conductor-protocol control plane
                                      (here the open-source dbos-relay) for recovery.
"""

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from dbos import DBOS, DBOSConfig
from fastapi import FastAPI
from poc_job import INTERVAL, JOB_SECONDS, finish_run, start_run

EXECUTOR = os.environ.get('POC_DBOS_EXECUTOR', 'default')
PEER_RECOVERY = os.environ.get('POC_DBOS_PEER_RECOVERY', '0').lower() in ('true', '1')
# DBOS.destroy's documented drain: wait this long for active workflows. Set just
# under the pod's termination grace period (50 s for a 60 s grace).
SHUTDOWN_SECONDS = int(os.environ.get('POC_DBOS_SHUTDOWN_SECONDS', '50'))
# ponytail: staleness stands in for liveness, which DBOS has no signal for without
# Conductor. A live workflow older than this would be run a second time.
STALE_AFTER = timedelta(seconds=JOB_SECONDS * 1.5)

config: DBOSConfig = {
    'name': 'tq-poc',
    'system_database_url': os.environ['POC_DATABASE_URL'],
    # Schema comes from the Alembic migration; poc_app cannot run DDL.
    'run_migrations': False,
}
if EXECUTOR == 'hostname':
    config['executor_id'] = socket.gethostname()
# A key alone connects to DBOS's hosted Conductor; a URL too points at a self-hosted one.
if os.environ.get('POC_DBOS_CONDUCTOR_KEY'):
    config['conductor_key'] = os.environ['POC_DBOS_CONDUCTOR_KEY']
if os.environ.get('POC_DBOS_CONDUCTOR_URL'):
    config['conductor_url'] = os.environ['POC_DBOS_CONDUCTOR_URL']
DBOS(config=config)


@DBOS.step()
async def start_step(job: str, slot: datetime) -> int:
    return await start_run(job, slot)


@DBOS.step()
async def finish_step(run_id: int) -> None:
    await finish_run(run_id)


@DBOS.workflow()
async def tick(scheduled_at: datetime, context: Any) -> None:
    await finish_step(await start_step('tick', scheduled_at))


def cron() -> str:
    # Six fields, seconds first.
    if INTERVAL < 60:
        return f'*/{INTERVAL} * * * * *'
    return f'0 */{INTERVAL // 60} * * * *'


async def recover_peers() -> None:
    while True:
        await asyncio.sleep(5)
        cutoff = (datetime.now(UTC) - STALE_AFTER).isoformat()
        stale = await DBOS.list_workflows_async(
            status='PENDING',
            dequeued_before=cutoff,
            load_input=False,
            load_output=False,
        )
        for wf in stale:
            if wf.executor_id != DBOS.executor_id:
                DBOS.logger.info(f'Resuming {wf.workflow_id} from {wf.executor_id}')
                await DBOS.resume_workflow_async(wf.workflow_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DBOS.launch()
    await DBOS.apply_schedules_async(
        [{'schedule_name': 'tick', 'workflow_fn': tick, 'schedule': cron()}]
    )
    task = asyncio.create_task(recover_peers()) if PEER_RECOVERY else None
    yield
    if task:
        task.cancel()
    # Off the event loop: DBOS runs async workflows on the loop that called
    # launch(), so a blocking destroy() here would freeze the workflows it waits on.
    await asyncio.to_thread(
        DBOS.destroy, workflow_completion_timeout_sec=SHUTDOWN_SECONDS
    )


app = FastAPI(lifespan=lifespan)
