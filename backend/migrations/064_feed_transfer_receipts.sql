-- One receipt per company bounds storage and fences stale concurrent transfers.
CREATE TABLE feed_transfer_receipts (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id),
    token TEXT NOT NULL,
    digest TEXT NOT NULL,
    saved INTEGER NOT NULL,
    saved_at TEXT NOT NULL
);
