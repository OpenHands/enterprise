-- Harness tables, plus an app role that cannot create schema objects. Candidate
-- replicas connect as poc_app, so any runtime DDL fails; candidate schema must
-- come from its Alembic migration, which connects as the owner (poc).
CREATE ROLE poc_app LOGIN PASSWORD 'poc_app';
GRANT CONNECT, TEMPORARY ON DATABASE poc TO poc_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO poc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE poc GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO poc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE poc GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO poc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE poc GRANT EXECUTE ON FUNCTIONS TO poc_app;

CREATE TABLE poc_job_runs (
    id bigserial PRIMARY KEY,
    job text NOT NULL,
    slot timestamptz NOT NULL,
    replica text NOT NULL,
    fired_at timestamptz NOT NULL,     -- the replica's clock (skewed in P2)
    started_at timestamptz NOT NULL DEFAULT now(),  -- the database clock
    finished_at timestamptz,
    finished_by text
);

CREATE TABLE poc_job_claims (
    job text NOT NULL,
    slot timestamptz NOT NULL,
    replica text NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job, slot)
);
