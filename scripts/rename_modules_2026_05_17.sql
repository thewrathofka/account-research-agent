-- One-time SQL migration for the 2026-05-17 module renumbering refactor.
--
-- Maps the old module identifiers in the live database (runs.db locally,
-- arr-runs-db on Turso) to the new sequential ones. Runs inside a single
-- transaction so a mid-flight failure rolls back cleanly.
--
-- Rename mapping (old → new):
--   module_03_revenue_model        → module_02_revenue_model
--   module_04_pain_points          → module_03_pain_points
--   module_05_corporate_structure  → module_04_corporate_structure
--   module_06_structural_news      → module_05_structural_news
--   module_07_trigger_events       → module_06_trigger_events
--   module_09_creative_reality     → module_07_creative_reality
--   module_10_ad_library           → module_08_ad_library
--   module_12_competitor_snapshot  → module_09_competitor_snapshot
--   module_13_industry_pulse       → module_10_industry_pulse
--   module_14_hiring_signal        → module_11_hiring_signal
--
-- Tables touched:
--   task_runs.task_name             — primary freshness/diff-detection key
--   event_alerts.module             — used in alert summary text
--   event_alerts.signature          — has module_NN prefix; rewrite prefix only
--
-- Usage:
--   sqlite3 runs.db < scripts/rename_modules_2026_05_17.sql
--   turso db shell arr-runs-db < scripts/rename_modules_2026_05_17.sql
--
-- Rollback (if needed BEFORE COMMIT releases the lock — local SQLite only):
--   .restore /tmp/runs.db.pre-rename.<DATE>.sql
-- For Turso, restore from `turso db shell arr-runs-db ".dump"` snapshot.

BEGIN TRANSACTION;

-- ---- task_runs.task_name ----
UPDATE task_runs SET task_name = 'module_02_revenue_model'        WHERE task_name = 'module_03_revenue_model';
UPDATE task_runs SET task_name = 'module_03_pain_points'          WHERE task_name = 'module_04_pain_points';
UPDATE task_runs SET task_name = 'module_04_corporate_structure'  WHERE task_name = 'module_05_corporate_structure';
UPDATE task_runs SET task_name = 'module_05_structural_news'      WHERE task_name = 'module_06_structural_news';
UPDATE task_runs SET task_name = 'module_06_trigger_events'       WHERE task_name = 'module_07_trigger_events';
UPDATE task_runs SET task_name = 'module_07_creative_reality'     WHERE task_name = 'module_09_creative_reality';
UPDATE task_runs SET task_name = 'module_08_ad_library'           WHERE task_name = 'module_10_ad_library';
UPDATE task_runs SET task_name = 'module_09_competitor_snapshot'  WHERE task_name = 'module_12_competitor_snapshot';
UPDATE task_runs SET task_name = 'module_10_industry_pulse'       WHERE task_name = 'module_13_industry_pulse';
UPDATE task_runs SET task_name = 'module_11_hiring_signal'        WHERE task_name = 'module_14_hiring_signal';

-- ---- event_alerts.module (full-string column) ----
UPDATE event_alerts SET module = 'module_02_revenue_model'        WHERE module = 'module_03_revenue_model';
UPDATE event_alerts SET module = 'module_03_pain_points'          WHERE module = 'module_04_pain_points';
UPDATE event_alerts SET module = 'module_04_corporate_structure'  WHERE module = 'module_05_corporate_structure';
UPDATE event_alerts SET module = 'module_05_structural_news'      WHERE module = 'module_06_structural_news';
UPDATE event_alerts SET module = 'module_06_trigger_events'       WHERE module = 'module_07_trigger_events';
UPDATE event_alerts SET module = 'module_07_creative_reality'     WHERE module = 'module_09_creative_reality';
UPDATE event_alerts SET module = 'module_08_ad_library'           WHERE module = 'module_10_ad_library';
UPDATE event_alerts SET module = 'module_09_competitor_snapshot'  WHERE module = 'module_12_competitor_snapshot';
UPDATE event_alerts SET module = 'module_10_industry_pulse'       WHERE module = 'module_13_industry_pulse';
UPDATE event_alerts SET module = 'module_11_hiring_signal'        WHERE module = 'module_14_hiring_signal';

-- ---- event_alerts.signature (prefix-replace via REPLACE() on full column) ----
-- Signatures look like "module_06:structure_note:bankruptcy:2026-05-10".
-- Only the numeric prefix changes; the rest is verbatim. REPLACE is safe
-- because the prefix string "module_NN:" can't appear mid-signature.
UPDATE event_alerts SET signature = REPLACE(signature, 'module_03:', 'module_02:') WHERE signature LIKE 'module_03:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_04:', 'module_03:') WHERE signature LIKE 'module_04:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_05:', 'module_04:') WHERE signature LIKE 'module_05:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_06:', 'module_05:') WHERE signature LIKE 'module_06:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_07:', 'module_06:') WHERE signature LIKE 'module_07:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_09:', 'module_07:') WHERE signature LIKE 'module_09:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_10:', 'module_08:') WHERE signature LIKE 'module_10:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_12:', 'module_09:') WHERE signature LIKE 'module_12:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_13:', 'module_10:') WHERE signature LIKE 'module_13:%';
UPDATE event_alerts SET signature = REPLACE(signature, 'module_14:', 'module_11:') WHERE signature LIKE 'module_14:%';

COMMIT;

-- Verification queries (run after COMMIT, won't affect data):
--   SELECT task_name, COUNT(*) FROM task_runs GROUP BY task_name ORDER BY task_name;
--     → expect no rows with old names (module_03_*, module_04_*, etc.); only 01, 02, ..., 11 and research_pass + company_overview.
--   SELECT module, COUNT(*) FROM event_alerts GROUP BY module ORDER BY module;
--     → expect no rows with old names.
--   SELECT signature FROM event_alerts WHERE signature LIKE 'module_03:%' OR signature LIKE 'module_04:%' OR signature LIKE 'module_12:%' OR signature LIKE 'module_13:%' OR signature LIKE 'module_14:%';
--     → expect zero rows.
