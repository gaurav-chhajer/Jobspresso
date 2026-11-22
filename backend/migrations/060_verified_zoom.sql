-- Enable the verified Zoom Workday feed while retaining its company ID.
UPDATE companies SET active=1 WHERE career_source='zoom' AND active=0;
