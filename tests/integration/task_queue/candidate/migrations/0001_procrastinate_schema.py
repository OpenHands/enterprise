"""Procrastinate 3.10.0 schema, applied by the owner; the app role only gets DML.

Revision ID: 0001
"""

from alembic import op
from procrastinate.schema import SchemaManager

revision = '0001'
down_revision = None

# poc_app runs the worker: it calls the procrastinate_* functions, and their
# triggers write to procrastinate_events as the caller, so it needs DML everywhere.
GRANTS = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT c.relname, c.relkind FROM pg_class c
             WHERE c.relnamespace = 'public'::regnamespace
               AND c.relname LIKE 'procrastinate%' AND c.relkind IN ('r', 'S') LOOP
        IF r.relkind = 'r' THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO poc_app', r.relname);
        ELSE
            EXECUTE format('GRANT USAGE, SELECT, UPDATE ON SEQUENCE %I TO poc_app', r.relname);
        END IF;
    END LOOP;
    FOR r IN SELECT p.oid::regprocedure AS fn FROM pg_proc p
             WHERE p.pronamespace = 'public'::regnamespace
               AND p.proname LIKE 'procrastinate%' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO poc_app', r.fn);
    END LOOP;
    FOR r IN SELECT t.typname FROM pg_type t
             WHERE t.typnamespace = 'public'::regnamespace
               AND t.typname LIKE 'procrastinate%' AND t.typtype IN ('e', 'c') LOOP
        EXECUTE format('GRANT USAGE ON TYPE %I TO poc_app', r.typname);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    # The same SQL `procrastinate schema --read` prints. Run it on the psycopg
    # connection directly: SQLAlchemy would parse its `:` and `%` as parameters.
    conn = op.get_bind().connection.driver_connection
    conn.execute(SchemaManager.get_schema())
    conn.execute(GRANTS)
