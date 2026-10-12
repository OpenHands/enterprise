"""The background worker, which runs the app's procrastinate jobs.

It runs as its own process, next to the app server: ``python -m
openhands.app_server.worker``. Nothing in the app server waits on it.
"""
