"""Keep the developer's environment out of the test run.

CI sets none of these variables, so clearing them makes every machine run the
tests the way CI does. They have to be cleared before any test module is
imported, because the code under test reads them at import time: for example,
``openhands.app_server.utils.dependencies`` reads ``SESSION_API_KEY`` once, and
routers add the session-key check while they are being built.
"""

from __future__ import annotations

import os

# Variables that ``migrations/`` read, other than the ``DB_``, ``GCP_`` and
# ``PG`` connection settings that ``run_alembic`` sets itself.
# test_ambient_env_test_hygiene.py fails if a migration reads one not listed.
MIGRATION_INPUTS = (
    'AUTH_URL',
    'AUTH_WEB_HOST',
    'AZURE_DEVOPS_CLIENT_ID',
    'AZURE_DEVOPS_CLIENT_SECRET',
    'AZURE_DEVOPS_TENANT_ID',
    'BITBUCKET_APP_CLIENT_ID',
    'BITBUCKET_APP_CLIENT_SECRET',
    'BITBUCKET_DATA_CENTER_CLIENT_ID',
    'BITBUCKET_DATA_CENTER_CLIENT_SECRET',
    'BITBUCKET_DATA_CENTER_HOST',
    'CREATE_DATABASE_IF_MISSING',
    'EMAIL_PATTERN_BLACKLIST',
    'EMAIL_PATTERN_WHITELIST',
    'ENABLE_INTEGRATED_IDP',
    'GITHUB_APP_CLIENT_ID',
    'GITHUB_APP_CLIENT_SECRET',
    'GITLAB_APP_CLIENT_ID',
    'GITLAB_APP_CLIENT_SECRET',
    'GITLAB_HOST',
    'KEYCLOAK_CLIENT_ID',
    'KEYCLOAK_CLIENT_SECRET',
    'KEYCLOAK_REALM_NAME',
    'KEYCLOAK_SERVER_URL',
    'KEYCLOAK_SERVER_URL_EXT',
    'STRIPE_API_KEY',
    'WEB_HOST',
)

AMBIENT_VARIABLES = ('SESSION_API_KEY', *MIGRATION_INPUTS)


def isolate_from_ambient_environment() -> None:
    for name in AMBIENT_VARIABLES:
        os.environ.pop(name, None)
    # litellm and openhands.app_server.shared call load_dotenv() on import,
    # which would put a developer's .env (staging credentials, by the
    # template's instructions) back into this process and every subprocess.
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
