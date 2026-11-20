PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS toolsets(id TEXT PRIMARY KEY, manifest TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_toolset ON toolsets(active) WHERE active=1;
CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
    path TEXT NOT NULL, domains TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS output_roots(id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, verified INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created_at REAL NOT NULL,
    kind TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, digest TEXT NOT NULL, response TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
    created_at REAL NOT NULL, url TEXT NOT NULL, provider TEXT NOT NULL, kind TEXT NOT NULL,
    state TEXT NOT NULL, desired_state TEXT NOT NULL DEFAULT 'run', reason TEXT,
    profile_id TEXT NOT NULL REFERENCES profiles(id), profile TEXT NOT NULL,
    credential_id TEXT REFERENCES credentials(id), toolset_id TEXT NOT NULL,
    output_root_id TEXT NOT NULL REFERENCES output_roots(id), fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1, next_eligible_at REAL NOT NULL DEFAULT 0,
    attempt_id TEXT, run_token TEXT, completed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0, counts_complete INTEGER NOT NULL DEFAULT 0,
    phase TEXT NOT NULL DEFAULT 'queued', progress TEXT NOT NULL DEFAULT '{}',
    crash_retries INTEGER NOT NULL DEFAULT 0, elapsed REAL NOT NULL DEFAULT 0,
    time_budget INTEGER NOT NULL DEFAULT 21600);
CREATE UNIQUE INDEX IF NOT EXISTS active_fingerprint ON sources(fingerprint)
    WHERE state NOT IN ('succeeded','partial','failed','cancelled');
CREATE INDEX IF NOT EXISTS source_queue ON sources(state,next_eligible_at);
CREATE INDEX IF NOT EXISTS source_history ON sources(created_at,id);
CREATE TABLE IF NOT EXISTS batch_sources(batch_id TEXT NOT NULL REFERENCES batches(id),
    source_id TEXT NOT NULL REFERENCES sources(id), ordinal INTEGER NOT NULL, shared INTEGER NOT NULL,
    PRIMARY KEY(batch_id,ordinal));
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    ordinal INTEGER NOT NULL, run_token TEXT NOT NULL UNIQUE, started_at REAL NOT NULL,
    ended_at REAL, exit_code INTEGER, result TEXT, owner TEXT, seq INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id,ordinal));
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, source_id TEXT,
    attempt_id TEXT, output_root_id TEXT NOT NULL REFERENCES output_roots(id), engine TEXT NOT NULL,
    media_key TEXT NOT NULL, representation_key TEXT NOT NULL, relative_path TEXT NOT NULL,
    size INTEGER NOT NULL, valid INTEGER NOT NULL DEFAULT 1, checked_at REAL NOT NULL,
    UNIQUE(output_root_id,engine,media_key,representation_key), UNIQUE(output_root_id,relative_path));
CREATE TABLE IF NOT EXISTS publish_intents(id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL, output_root_id TEXT NOT NULL, engine TEXT NOT NULL,
    media_key TEXT NOT NULL, representation_key TEXT NOT NULL, candidate_path TEXT NOT NULL,
    final_path TEXT NOT NULL, size INTEGER NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS pending_publications ON publish_intents(state,output_root_id);
CREATE TABLE IF NOT EXISTS provider_limits(provider TEXT PRIMARY KEY, cooldown_until REAL NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS output_quarantine(output_root_id TEXT NOT NULL,source_id TEXT NOT NULL,
    reason TEXT NOT NULL,PRIMARY KEY(output_root_id,source_id));
CREATE TABLE IF NOT EXISTS toolset_validation(id TEXT PRIMARY KEY,verified INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS publication_receipts(attempt_id TEXT NOT NULL,receipt_key TEXT NOT NULL,
    intent_id TEXT NOT NULL UNIQUE REFERENCES publish_intents(id),PRIMARY KEY(attempt_id,receipt_key));
CREATE TABLE IF NOT EXISTS policy_skips(source_id TEXT NOT NULL REFERENCES sources(id),
    media_key TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL,
    PRIMARY KEY(source_id,media_key));
PRAGMA user_version=2;
