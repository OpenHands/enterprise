# Build context is the task_queue directory: the standard candidate plus RedBeat.
FROM tq-poc-base
COPY candidate/requirements.txt /app/requirements.txt
COPY candidate_redbeat/requirements.txt /app/requirements-redbeat.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-redbeat.txt
COPY candidate /app
COPY candidate_redbeat/redbeat_app.py /app/
