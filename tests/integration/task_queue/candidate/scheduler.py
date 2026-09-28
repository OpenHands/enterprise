"""Setup 3a: the only scheduler. Enqueues one message per occurrence."""

from apscheduler.schedulers.blocking import BlockingScheduler
from poc_job import slot_for
from tasks import TRIGGER, tick


def fire() -> None:
    tick.send(slot_for().isoformat())


scheduler = BlockingScheduler(timezone='UTC')
scheduler.add_job(fire, TRIGGER)
scheduler.start()
