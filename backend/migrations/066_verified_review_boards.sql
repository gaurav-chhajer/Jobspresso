-- All 21 review boards passed native fetch and full-description verification.
-- Resolve employer identity by verified board, never by a generic company name.
WITH verified(name, ats_type, board_slug, legacy_slug) AS (
VALUES
    ('Axiom Law', 'greenhouse', 'axiom', NULL),
    ('BlueCloud', 'lever', 'blue', NULL),
    ('Complete', 'ashby', 'complete', 'yc-employer-complete'),
    ('Double (Bookkeeping)', 'ashby', 'double', NULL),
    ('Echo Neurotechnologies', 'greenhouse', 'echo', NULL),
    ('Flint (Marketing AI)', 'ashby', 'flint', NULL),
    ('Glimpse', 'ashby', 'glimpse', 'yc-employer-glimpse-2'),
    ('Harbor (AI Insurance)', 'ashby', 'harbor', NULL),
    ('Hazel Health', 'greenhouse', 'hazel', NULL),
    ('Lance', 'ashby', 'lance', 'yc-employer-lance'),
    ('Latent (Pharmacy AI)', 'ashby', 'latent', NULL),
    ('Monogram (AI Interface)', 'ashby', 'monogram', NULL),
    ('Nectar Social', 'ashby', 'nectar', NULL),
    ('Pocket', 'ashby', 'pocket', 'yc-employer-pocket'),
    ('Porter Cares', 'lever', 'porter', NULL),
    ('Porter Works', 'greenhouse', 'porter', NULL),
    ('Pure', 'ashby', 'pure', 'yc-employer-pure'),
    ('Rain (Stablecoin Payments)', 'ashby', 'rain', NULL),
    ('Thera', 'ashby', 'thera', 'yc-employer-thera'),
    ('Vector (Logistics)', 'ashby', 'vector', NULL),
    ('Veo Technologies', 'lever', 'veo', NULL)
)
UPDATE companies SET
    name=(SELECT v.name FROM verified v WHERE v.ats_type=companies.ats_type AND lower(v.board_slug)=lower(companies.board_slug)),
    active=1, discovery_only=0
WHERE career_source IS NULL AND EXISTS (
    SELECT 1 FROM verified v WHERE v.ats_type=companies.ats_type
      AND lower(v.board_slug)=lower(companies.board_slug)
);

WITH verified(name, ats_type, board_slug, legacy_slug) AS (
VALUES
    ('Axiom Law', 'greenhouse', 'axiom', NULL),
    ('BlueCloud', 'lever', 'blue', NULL),
    ('Complete', 'ashby', 'complete', 'yc-employer-complete'),
    ('Double (Bookkeeping)', 'ashby', 'double', NULL),
    ('Echo Neurotechnologies', 'greenhouse', 'echo', NULL),
    ('Flint (Marketing AI)', 'ashby', 'flint', NULL),
    ('Glimpse', 'ashby', 'glimpse', 'yc-employer-glimpse-2'),
    ('Harbor (AI Insurance)', 'ashby', 'harbor', NULL),
    ('Hazel Health', 'greenhouse', 'hazel', NULL),
    ('Lance', 'ashby', 'lance', 'yc-employer-lance'),
    ('Latent (Pharmacy AI)', 'ashby', 'latent', NULL),
    ('Monogram (AI Interface)', 'ashby', 'monogram', NULL),
    ('Nectar Social', 'ashby', 'nectar', NULL),
    ('Pocket', 'ashby', 'pocket', 'yc-employer-pocket'),
    ('Porter Cares', 'lever', 'porter', NULL),
    ('Porter Works', 'greenhouse', 'porter', NULL),
    ('Pure', 'ashby', 'pure', 'yc-employer-pure'),
    ('Rain (Stablecoin Payments)', 'ashby', 'rain', NULL),
    ('Thera', 'ashby', 'thera', 'yc-employer-thera'),
    ('Vector (Logistics)', 'ashby', 'vector', NULL),
    ('Veo Technologies', 'lever', 'veo', NULL)
)
UPDATE companies SET
    ats_type=(SELECT v.ats_type FROM verified v WHERE v.legacy_slug=companies.board_slug),
    board_slug=(SELECT v.board_slug FROM verified v WHERE v.legacy_slug=companies.board_slug),
    active=1, discovery_only=0
WHERE ats_type='greenhouse' AND discovery_only=1 AND career_source IS NULL
  AND EXISTS (SELECT 1 FROM verified v WHERE v.legacy_slug=companies.board_slug)
  AND NOT EXISTS (
      SELECT 1 FROM companies other JOIN verified v
        ON other.ats_type=v.ats_type AND lower(other.board_slug)=lower(v.board_slug)
      WHERE v.legacy_slug=companies.board_slug AND other.id<>companies.id
  );

WITH verified(name, ats_type, board_slug, legacy_slug) AS (
VALUES
    ('Axiom Law', 'greenhouse', 'axiom', NULL),
    ('BlueCloud', 'lever', 'blue', NULL),
    ('Complete', 'ashby', 'complete', 'yc-employer-complete'),
    ('Double (Bookkeeping)', 'ashby', 'double', NULL),
    ('Echo Neurotechnologies', 'greenhouse', 'echo', NULL),
    ('Flint (Marketing AI)', 'ashby', 'flint', NULL),
    ('Glimpse', 'ashby', 'glimpse', 'yc-employer-glimpse-2'),
    ('Harbor (AI Insurance)', 'ashby', 'harbor', NULL),
    ('Hazel Health', 'greenhouse', 'hazel', NULL),
    ('Lance', 'ashby', 'lance', 'yc-employer-lance'),
    ('Latent (Pharmacy AI)', 'ashby', 'latent', NULL),
    ('Monogram (AI Interface)', 'ashby', 'monogram', NULL),
    ('Nectar Social', 'ashby', 'nectar', NULL),
    ('Pocket', 'ashby', 'pocket', 'yc-employer-pocket'),
    ('Porter Cares', 'lever', 'porter', NULL),
    ('Porter Works', 'greenhouse', 'porter', NULL),
    ('Pure', 'ashby', 'pure', 'yc-employer-pure'),
    ('Rain (Stablecoin Payments)', 'ashby', 'rain', NULL),
    ('Thera', 'ashby', 'thera', 'yc-employer-thera'),
    ('Vector (Logistics)', 'ashby', 'vector', NULL),
    ('Veo Technologies', 'lever', 'veo', NULL)
)
INSERT INTO companies(id,name,ats_type,board_slug,active,discovery_only)
SELECT (SELECT MAX(100000,COALESCE(MAX(id),0)) FROM companies)
       + ROW_NUMBER() OVER (ORDER BY v.name),
       v.name,v.ats_type,v.board_slug,1,0
FROM verified v
WHERE NOT EXISTS (
    SELECT 1 FROM companies c WHERE c.ats_type=v.ats_type
      AND lower(c.board_slug)=lower(v.board_slug)
);
