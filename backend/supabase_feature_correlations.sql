-- Feature x Regime x Horizon hit-rate (Harness Engineering Phase 2, Supabase).
--
-- Mirrors backend/feature_correlations.py's SQLite view so production
-- (Supabase/Postgres) can back the same finance-superintelligence correlation
-- analytics without shipping per-row Python aggregation. Install once via:
--
--   psql $SUPABASE_DB_URL -f backend/supabase_feature_correlations.sql
--
-- The MATERIALIZED VIEW is refreshable; schedule with pg_cron or trigger from
-- outcome_grader.run_grader_pass once it runs in the cloud workflow.

DROP MATERIALIZED VIEW IF EXISTS v_feature_hit_rate CASCADE;

CREATE MATERIALIZED VIEW v_feature_hit_rate AS
SELECT
    f.feature_name                                 AS feature_name,
    COALESCE(NULLIF(f.value_str, ''), '')          AS feature_value,
    o.horizon                                      AS horizon,
    COALESCE(NULLIF(f.regime, ''), '')             AS regime,
    COUNT(*)                                       AS n,
    AVG(o.excess_return)                           AS mean_excess_return,
    AVG(
        CASE
            WHEN o.correct_bool = 1 THEN 1.0
            WHEN o.correct_bool = 0 THEN 0.0
        END
    )                                              AS hit_rate,
    SUM(CASE WHEN o.correct_bool IS NOT NULL THEN 1 ELSE 0 END) AS n_labelled
FROM feature_snapshots f
JOIN outcome_observations o ON o.decision_id = f.decision_id
WHERE o.metric = 'excess_return'
GROUP BY f.feature_name, feature_value, o.horizon, regime;

-- Supports fast filtering from the FastAPI resource router.
CREATE INDEX IF NOT EXISTS idx_vfhr_horizon
    ON v_feature_hit_rate (horizon);
CREATE INDEX IF NOT EXISTS idx_vfhr_feature_horizon
    ON v_feature_hit_rate (feature_name, horizon);
CREATE INDEX IF NOT EXISTS idx_vfhr_regime
    ON v_feature_hit_rate (regime);
