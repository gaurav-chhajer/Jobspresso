-- Register the verified career feeds for hosted worker discovery.
WITH verified(source, name) AS (
    VALUES
        ('apryse', 'Apryse'),
        ('twilio', 'Twilio'),
        ('ocrolus', 'Ocrolus'),
        ('chainguard', 'Chainguard'),
        ('minio', 'MinIO'),
        ('renaissance', 'Renaissance Learning'),
        ('logicgate', 'LogicGate'),
        ('faire', 'Faire'),
        ('cengage', 'Cengage Group'),
        ('vgs', 'VGS (Very Good Security)'),
        ('usergems', 'UserGems'),
        ('abnormal', 'Abnormal AI'),
        ('cloudbeds', 'Cloudbeds'),
        ('missionwired', 'MissionWired'),
        ('patreon', 'Patreon'),
        ('teikametrics', 'Teikametrics'),
        ('workera', 'Workera'),
        ('valtech', 'Valtech'),
        ('schneidergeospatial', 'Schneider Geospatial'),
        ('protective', 'Protective Life'),
        ('phdata', 'phData'),
        ('upstart', 'Upstart'),
        ('akamai', 'Akamai Technologies'),
        ('awesomemotive', 'Awesome Motive'),
        ('docker', 'Docker'),
        ('himsandhers', 'Hims & Hers'),
        ('constructor', 'Constructor'),
        ('resilinc', 'Resilinc'),
        ('workiva', 'Workiva'),
        ('kong', 'Kong')
)
INSERT INTO companies(id, name, ats_type, board_slug, career_source)
SELECT (SELECT MAX(100000, COALESCE(MAX(id),0)) FROM companies)
       + ROW_NUMBER() OVER (ORDER BY v.source),
       v.name, 'greenhouse', 'career-site-' || v.source, v.source
FROM verified v
WHERE NOT EXISTS (SELECT 1 FROM companies c WHERE c.career_source = v.source);
