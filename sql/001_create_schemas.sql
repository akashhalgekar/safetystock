-- Layer separation.
--   raw     : immutable, mirrors the source files exactly, defects included
--   staging : typed and cleaned, every exclusion counted and recorded
--   mart    : modelled for the question being asked
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS mart;
