# Base image for every candidate: the stub job, the run-once guard, Alembic,
# and libfaketime so P2 can skew one replica's clock.
FROM python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
RUN apt-get update \
    && apt-get install -y --no-install-recommends faketime \
    && ln -s "$(find /usr/lib -name libfaketime.so.1 | head -n1)" /usr/local/lib/libfaketime.so.1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /harness/requirements.txt
RUN pip install --no-cache-dir -r /harness/requirements.txt
COPY poc_job.py alembic.ini /harness/
COPY alembic /harness/alembic
ENV PYTHONPATH=/harness:/app PYTHONUNBUFFERED=1
WORKDIR /app
