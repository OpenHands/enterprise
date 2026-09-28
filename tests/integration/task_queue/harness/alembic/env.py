import os

from alembic import context
from sqlalchemy import create_engine

url = os.environ['POC_MIGRATE_URL'].replace('postgresql://', 'postgresql+psycopg://')
with create_engine(url).connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
