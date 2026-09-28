"""Stand-in for the app: serves HTTP and schedules nothing. Supercronic runs the jobs."""

from fastapi import FastAPI

app = FastAPI()
