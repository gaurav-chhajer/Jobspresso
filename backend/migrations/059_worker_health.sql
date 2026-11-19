CREATE TABLE worker_health (
    worker TEXT NOT NULL,
    company_id INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_success_at TEXT,
    job_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(worker, company_id)
);
