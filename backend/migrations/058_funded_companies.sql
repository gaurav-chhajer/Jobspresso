-- Recent funding events from public news feeds. company_id is set only when a
-- Greenhouse board with the same company name was verified; NULL means lead only.
CREATE TABLE funded_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    headline TEXT NOT NULL,
    news_url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    company_id INTEGER REFERENCES companies(id)
);
CREATE INDEX funded_companies_published ON funded_companies(published_at DESC);
