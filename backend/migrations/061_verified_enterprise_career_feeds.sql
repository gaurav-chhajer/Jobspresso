-- Add the verified public Standard Chartered, TIAA, Nike, NielsenIQ and Whatfix feeds.
ALTER TABLE companies RENAME COLUMN career_source TO career_source_v61_legacy;
ALTER TABLE companies ADD COLUMN career_source TEXT CHECK (career_source IS NULL OR career_source IN ('synechron','hsbc','postman','cvent','zs','icici','wipro','idfc','futuresfirst','sapient','hotstar','dpworld','bofa','jpmorgan','morganstanley','barclays','nomura','pwc','google','amazon','apple','netflix','microsoft','meta','salesforce','adobe','slack','airbnb','notion','zoom','supabase','camunda','livekit','cloudlinux','clutch','fingerprint','clearer','instrumentl','xapo','sourcegraph','passion','duckduckgo','safetywing','alpaca','atticus','counterpart','happyco','metabase','paradox','superside','coinmarketcap','vrchat','raya','canonical','circle','uscreen','kindred','wishpond','kinsta','assemblyai','highlevel','gitlab','grafana','wikimedia','remote','atlassian','toptal','deel','elastic','zapier','automattic','shopify','github','drivetrain','smartworking','sprinto','jobgether','weekday','yc','apryse','twilio','ocrolus','chainguard','minio','renaissance','logicgate','faire','cengage','vgs','usergems','abnormal','cloudbeds','missionwired','patreon','teikametrics','workera','valtech','schneidergeospatial','protective','phdata','upstart','akamai','awesomemotive','docker','himsandhers','constructor','resilinc','workiva','kong','standardchartered','tiaa','nike','nielseniq','whatfix'));
UPDATE companies SET career_source=career_source_v61_legacy;
CREATE UNIQUE INDEX companies_career_source_v61 ON companies(career_source) WHERE career_source IS NOT NULL;

WITH verified(source,name) AS (
    VALUES ('standardchartered','Standard Chartered'), ('tiaa','TIAA'), ('nike','Nike'), ('nielseniq','NielsenIQ'), ('whatfix','Whatfix')
)
INSERT INTO companies (id,name,ats_type,board_slug,career_source)
SELECT (SELECT MAX(100000,COALESCE(MAX(id),0)) FROM companies)
    + ROW_NUMBER() OVER (ORDER BY v.source), v.name, 'greenhouse', 'career-site-' || v.source, v.source
FROM verified v
WHERE NOT EXISTS (
    SELECT 1 FROM companies c WHERE c.career_source=v.source OR c.name=v.name COLLATE NOCASE
);
