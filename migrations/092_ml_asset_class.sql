-- 092_ml_asset_class.sql — Phase B(a): second categorical for the pooled
-- model (sector / equity_broad / bond / commodity / fx — ml/universe.py).
-- Apply via:  py apply_migration.py migrations/092_ml_asset_class.sql

ALTER TABLE ml_features ADD COLUMN IF NOT EXISTS asset_class text;
