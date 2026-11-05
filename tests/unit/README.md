## Introduction

This folder contains unit tests that could be run locally.

Tests that touch the database run against real PostgreSQL, so Docker needs to be running. The first such test
starts a `postgres:16` container, migrates a template database with `alembic upgrade head`, and gives every test
its own clone of it. See [`tests/postgres_testdb.py`](../postgres_testdb.py) for the details.

Run all test:

```bash
uv run pytest ./tests/unit
```

Run specific test file:

```bash
uv run pytest ./tests/unit/test_analytics_service.py
```

Run specific unit test

```bash
uv run pytest ./tests/unit/test_analytics_service.py::TestAnalyticsService::test_something
```

For a more verbose output, to above calls the `-v` flag can be used (even more verbose: `-vv` and `-vvv`):

```bash
uv run pytest -v ./tests/unit/test_analytics_service.py
```

More details see [pytest doc](https://docs.pytest.org/en/latest/contents.html)
