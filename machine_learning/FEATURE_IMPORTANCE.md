# PricePilot fee model — feature importance

Gain-based feature importance for the LightGBM fee model (`pricepilot_fee_model`),
as trained by the `pricepilot_fee_model_training` Keboola transformation on the
historical-quote table `pricing_fee_model_input`.

- **Gain** = total reduction in training loss attributed to splits on that feature
  (higher = more influential). **Share** = each feature's gain as a % of the total.
- Source of truth (regenerated on every retrain): Keboola table
  `out.c-pricing_ml.fee_model_importance` (and metrics in `out.c-pricing_ml.fee_model_metrics`).
- Raw values for this snapshot: [`fee_model_importance.csv`](./fee_model_importance.csv).

_Snapshot date: 2026-06-02._

| Rank | Feature | Gain | Share |
|---:|---|---:|---:|
| 1 | `base_fee` | 39,954 | 22.5% |
| 2 | `turn_around_time` | 32,945 | 18.5% |
| 3 | `building_area` | 16,174 | 9.1% |
| 4 | `land_acreage` | 15,372 | 8.6% |
| 5 | `secondary_property_type` | 12,159 | 6.8% |
| 6 | `created_month` | 11,865 | 6.7% |
| 7 | `primary_property_type` | 11,849 | 6.7% |
| 8 | `land_area` | 10,328 | 5.8% |
| 9 | `service_type_id` | 5,243 | 2.9% |
| 10 | `number_of_buildings` | 3,966 | 2.2% |
| 11 | `total_units` | 3,573 | 2.0% |
| 12 | `country` | 3,369 | 1.9% |
| 13 | `number_of_stories` | 2,744 | 1.5% |
| 14 | `site_complexity` | 1,954 | 1.1% |
| 15 | `busy_season_flag` | 1,664 | 0.9% |
| 16 | `limit_of_liability_tier` | 1,614 | 0.9% |
| 17 | `created_quarter` | 1,613 | 0.9% |
| 18 | `pct_units_inspect` | 1,337 | 0.8% |
| 19 | `prior_report` | 0 | 0.0% |

## How to read it

- **`base_fee` + `turn_around_time` ≈ 41%** of the signal — pricing is anchored to the
  service base price and turnaround speed, matching the rule engine's logic.
- **Size drivers** (`building_area`, `land_acreage`, `land_area`) ≈ 23% combined.
- **Property type** (`secondary_property_type` + `primary_property_type`) ≈ 13.5%.
- **`prior_report` = 0** — no predictive value in the current data (likely too sparse or
  constant); a candidate to drop.
- **Seasonality is weak** — `created_month` appears but `busy_season_flag` / `created_quarter`
  are minor, so the "busy level" feature contributes little.

## Regenerating

Re-run the `pricepilot_fee_model_training` transformation in Keboola. It rewrites
`out.c-pricing_ml.fee_model_importance`; copy the latest values here (and bump the snapshot
date) to keep this file in sync with the deployed model.
