-- Register direct ATS boards using the existing Greenhouse and Ashby adapters.
WITH verified(name, ats_type, board_slug) AS (
    VALUES
        ('Neo4j', 'greenhouse', 'neo4j'),
        ('Zscaler', 'greenhouse', 'zscaler'),
        ('MongoDB', 'greenhouse', 'mongodb'),
        ('Databricks', 'greenhouse', 'databricks'),
        ('Okta', 'greenhouse', 'okta'),
        ('Coupang', 'greenhouse', 'coupang'),
        ('Genea', 'greenhouse', 'genea'),
        ('Rubrik', 'greenhouse', 'rubrik'),
        ('OpenAI', 'ashby', 'openai'),
        ('SpotOn', 'ashby', 'spoton'),
        ('Weave', 'ashby', 'weave'),
        ('Deepgram', 'ashby', 'deepgram'),
        ('Snowflake', 'ashby', 'snowflake'),
        ('BusPatrol', 'ashby', 'buspatrol')
)
INSERT INTO companies(id, name, ats_type, board_slug)
SELECT (SELECT MAX(100000, COALESCE(MAX(id),0)) FROM companies)
       + ROW_NUMBER() OVER (ORDER BY v.board_slug), v.name, v.ats_type, v.board_slug
FROM verified v
WHERE NOT EXISTS (
    SELECT 1 FROM companies c
    WHERE lower(c.name) = lower(v.name)
       OR (c.ats_type = v.ats_type AND lower(c.board_slug) = lower(v.board_slug))
);
