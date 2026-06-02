# PricePilot fee model — run log

Append-only history of every `pricepilot_fee_model_training` run, newest first.
Use it to compare model versions over time (accuracy, feature count, data size).

- Source of truth (one row appended per retrain): Keboola table `out.c-pricing_ml.fee_model_runs` (primary key `run_id`).
- Raw values: [`fee_model_runs.csv`](./fee_model_runs.csv).
- Metrics are on the 20% hold-out test split; lower error / higher "within X%" and R² is better.

_Auto-synced from Keboola on 2026-06-02 by `sync_model_artifacts.py`._

| Run (UTC) | Tag | # feats | Rows (train) | Rows (test) | MAE | RMSE | Median err | Mean err | Within 10% | Within 20% | Test R² | Train R² |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-02 23:10 | no_base_fee | 14 | 141,476 | 28,296 | $530 | $973 | 11.3% | 23.0% | 45.5% | 72.5% | 0.475 | 0.568 |
| 2026-06-02 22:47 | prod | 15 | 141,476 | 28,296 | $530 | $974 | 11.4% | 23.0% | 45.4% | 72.5% | 0.475 | 0.570 |

## Feature set per run

- **2026-06-02 23:10** · _no_base_fee_ (14 features): `turn_around_time`, `building_area`, `land_acreage`, `total_units`, `pct_units_inspect`, `number_of_stories`, `number_of_buildings`, `created_month`, `service_type_id`, `primary_property_type`, `secondary_property_type`, `prior_report`, `site_complexity`, `country`
- **2026-06-02 22:47** · _prod_ (15 features): `base_fee`, `turn_around_time`, `building_area`, `land_acreage`, `total_units`, `pct_units_inspect`, `number_of_stories`, `number_of_buildings`, `created_month`, `service_type_id`, `primary_property_type`, `secondary_property_type`, `prior_report`, `site_complexity`, `country`
