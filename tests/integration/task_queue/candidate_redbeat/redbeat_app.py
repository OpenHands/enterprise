"""The standard Celery app, scheduled by RedBeat so several Beat replicas can run."""

from tasks import app

app.conf.update(
    beat_scheduler='redbeat.RedBeatScheduler',
    # Defaults are a 300 s loop and a 1500 s lock, so a standby takes up to
    # 25 minutes to take over. RedBeat's docs have these two knobs for that.
    beat_max_loop_interval=5,
    redbeat_lock_timeout=30,
)
