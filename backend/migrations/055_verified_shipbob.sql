-- ShipBob publishes its jobs under the shipbobinc Greenhouse board.
INSERT INTO companies(id, name, ats_type, board_slug)
SELECT (SELECT MAX(100000, COALESCE(MAX(id),0)) FROM companies) + 1,
       'ShipBob', 'greenhouse', 'shipbobinc'
WHERE NOT EXISTS (
    SELECT 1 FROM companies
    WHERE lower(name) IN ('shipbob', 'shipbob, inc.')
       OR (ats_type = 'greenhouse' AND lower(board_slug) = 'shipbobinc')
);
