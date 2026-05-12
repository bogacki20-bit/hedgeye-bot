-- migrations/202605110001_bridge_commands.sql
-- Postgres-backed command queue for command_bridge.py.
-- Dispatch / Cowork / any future agent INSERTs rows; the bridge polls and writes results back.

CREATE TABLE IF NOT EXISTS bridge_commands (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT        NOT NULL,                            -- 'ps_exec' | 'git_op' | ...
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status        TEXT        NOT NULL DEFAULT 'pending',          -- pending | running | done | error
    result        JSONB,
    error         TEXT,
    requester     TEXT,                                            -- 'dispatch', 'cowork', 'cli', etc.
    host          TEXT,                                            -- which bridge picked it up (hostname)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    timeout_s     INTEGER     NOT NULL DEFAULT 120
);

-- Fast-path lookup for the bridge poll loop.
CREATE INDEX IF NOT EXISTS bridge_commands_pending_idx
    ON bridge_commands (created_at)
    WHERE status = 'pending';

-- Audit / debugging lookup by requester.
CREATE INDEX IF NOT EXISTS bridge_commands_requester_idx
    ON bridge_commands (requester, created_at DESC);

-- Status sanity constraint.
ALTER TABLE bridge_commands
    DROP CONSTRAINT IF EXISTS bridge_commands_status_check;
ALTER TABLE bridge_commands
    ADD  CONSTRAINT bridge_commands_status_check
    CHECK (status IN ('pending', 'running', 'done', 'error', 'timeout', 'cancelled'));
