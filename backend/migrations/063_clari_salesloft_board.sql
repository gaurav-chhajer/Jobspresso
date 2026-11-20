-- Clari directs applicants to the combined Clari + Salesloft careers site.
-- Preserve the company ID and links from existing jobs and preferences.
UPDATE companies
SET ats_type='greenhouse', board_slug='salesloft'
WHERE lower(name)='clari' AND ats_type='lever' AND board_slug='clari'
  AND career_source IS NULL AND discovery_only=0;
