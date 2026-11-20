-- Enable the verified Uscreen BambooHR board without changing company IDs.
UPDATE companies SET active=1 WHERE career_source='uscreen' AND active=0;
