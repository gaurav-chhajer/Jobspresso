-- ClickHouse publishes its current openings on Ashby.
INSERT INTO companies(id, name, ats_type, board_slug)
SELECT (SELECT MAX(100000, COALESCE(MAX(id),0)) FROM companies) + 1,
       'ClickHouse', 'ashby', 'clickhouse'
WHERE NOT EXISTS (
    SELECT 1 FROM companies
    WHERE lower(name) = 'clickhouse'
       OR (ats_type = 'ashby' AND lower(board_slug) = 'clickhouse')
);
