#!/bin/sh
# Writes the crontab from POC_INTERVAL_SECONDS, then execs Supercronic as PID 1
# so it receives SIGTERM directly. Seven fields = seconds-first extended syntax.
set -eu
n="${POC_INTERVAL_SECONDS:-10}"
if [ "$n" -lt 60 ]; then
  spec="*/$n * * * * * *"
else
  spec="0 */$((n / 60)) * * * * *"
fi
echo "$spec python -m poc_job tick" > /tmp/crontab
cat /tmp/crontab
exec supercronic /tmp/crontab
