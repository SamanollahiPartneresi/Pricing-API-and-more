# PricePilot fee model — feature importance

Gain-based feature importance for the LightGBM fee model (`pricepilot_fee_model`),
trained by the `pricepilot_fee_model_training` Keboola transformation on the
historical-quote table `pricing_fee_model_input`.

- **Gain** = total reduction in training loss attributed to splits on that feature
  (higher = more influential). **Share** = each feature's gain as a % of the total.
- Source of truth (regenerated on every retrain): Keboola table `out.c-pricing_ml.fee_model_importance`.
- Raw values: [`fee_model_importance.csv`](./fee_model_importance.csv).

_Auto-synced from Keboola on 2026-06-30 by `sync_model_artifacts.py`._

| Rank | Feature | Gain | Share |
|---:|---|---:|---:|
| 1 | `service_type_id` | 44,687 | 22.7% |
| 2 | `turn_around_time` | 33,498 | 17.0% |
| 3 | `customer_type` | 26,727 | 13.6% |
| 4 | `land_acreage` | 20,895 | 10.6% |
| 5 | `building_area` | 15,058 | 7.6% |
| 6 | `secondary_property_type` | 14,024 | 7.1% |
| 7 | `primary_property_type` | 13,465 | 6.8% |
| 8 | `created_month` | 12,121 | 6.1% |
| 9 | `number_of_buildings` | 4,271 | 2.2% |
| 10 | `total_units` | 3,748 | 1.9% |
| 11 | `country` | 3,043 | 1.5% |
| 12 | `number_of_stories` | 2,437 | 1.2% |
| 13 | `site_complexity` | 1,953 | 1.0% |
| 14 | `pct_units_inspect` | 1,192 | 0.6% |
| 15 | `prior_report` | 0 | 0.0% |
