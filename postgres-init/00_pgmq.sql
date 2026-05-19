-- Auto-loaded by postgres on first initialization of the data volume.
-- Ensures the pgmq extension is available before the backend boots.
-- Idempotent: subsequent restarts skip this (init scripts run only when
-- the data dir is empty), but `CREATE EXTENSION IF NOT EXISTS` is safe.
CREATE EXTENSION IF NOT EXISTS pgmq;
