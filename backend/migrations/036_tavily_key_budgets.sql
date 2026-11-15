CREATE TABLE tavily_key_budget (
    key_slot INTEGER PRIMARY KEY CHECK(key_slot BETWEEN 1 AND 3),
    month TEXT NOT NULL,
    monthly_used INTEGER NOT NULL CHECK(monthly_used>=0)
);
-- Historical reservations did not store key identity. Conservatively charge
-- their aggregate usage to each slot rather than reset spent allowances.
INSERT INTO tavily_key_budget(key_slot,month,monthly_used)
SELECT 1,month,monthly_used FROM tavily_budget;
INSERT INTO tavily_key_budget(key_slot,month,monthly_used)
SELECT 2,month,monthly_used FROM tavily_budget;
INSERT INTO tavily_key_budget(key_slot,month,monthly_used)
SELECT 3,month,monthly_used FROM tavily_budget;
ALTER TABLE tavily_searches ADD COLUMN key_slot INTEGER CHECK(key_slot BETWEEN 1 AND 3);
