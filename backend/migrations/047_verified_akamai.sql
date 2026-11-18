-- Add verified Akamai Oracle Recruiting feed.
-- Add verified phData and Upstart Greenhouse feeds.
-- Add verified Protective Life Lever feed.
-- Add verified Valtech and Schneider Geospatial career feeds.
-- Add verified Teikametrics and Workera career feeds.
-- Add verified Cloudbeds, MissionWired, and Patreon career feeds.
-- Add verified employer ATS feeds while retaining existing company references.
ALTER TABLE companies RENAME COLUMN career_source TO career_source_v47_legacy;
ALTER TABLE companies ADD COLUMN career_source TEXT CHECK (career_source IS NULL OR career_source IN ('synechron','hsbc','postman','cvent','zs','icici','wipro','idfc','futuresfirst','sapient','hotstar','dpworld','bofa','jpmorgan','morganstanley','barclays','nomura','pwc','google','amazon','apple','netflix','microsoft','meta','salesforce','adobe','slack','airbnb','notion','zoom','supabase','camunda','livekit','cloudlinux','clutch','fingerprint','clearer','instrumentl','xapo','sourcegraph','passion','duckduckgo','safetywing','alpaca','atticus','counterpart','happyco','metabase','paradox','superside','coinmarketcap','vrchat','raya','canonical','circle','uscreen','kindred','wishpond','kinsta','assemblyai','highlevel','gitlab','grafana','wikimedia','remote','atlassian','toptal','deel','elastic','zapier','automattic','shopify','github','drivetrain','smartworking','sprinto','jobgether','weekday','yc','apryse','twilio','ocrolus','chainguard','minio','renaissance','logicgate','faire','cengage','vgs','usergems','abnormal','cloudbeds','missionwired','patreon','teikametrics','workera','valtech','schneidergeospatial','protective','phdata','upstart','akamai'));
UPDATE companies SET career_source=career_source_v47_legacy;
CREATE UNIQUE INDEX companies_career_source_v20 ON companies(career_source) WHERE career_source IS NOT NULL;
