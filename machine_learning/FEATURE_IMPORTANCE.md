# PricePilot fee model — feature importance

Gain-based feature importance for the LightGBM fee model (`pricepilot_fee_model`),
trained by the `pricepilot_fee_model_training` Keboola transformation on the
historical-quote table `pricing_fee_model_input`.

- **Gain** = total reduction in training loss attributed to splits on that feature
  (higher = more influential). **Share** = each feature's gain as a % of the total.
- Source of truth (regenerated on every retrain): Keboola table `out.c-pricing_ml.fee_model_importance`.
- Raw values: [`fee_model_importance.csv`](./fee_model_importance.csv).

_Auto-synced from Keboola on 2026-06-13 by `sync_model_artifacts.py`._

| Rank | Feature | Gain | Share |
|---:|---|---:|---:|
| 1 | `service_type_id` | 44,404 | 25.5% |
| 2 | `turn_around_time` | 33,235 | 19.1% |
| 3 | `land_acreage` | 21,367 | 12.3% |
| 4 | `building_area` | 17,336 | 10.0% |
| 5 | `created_month` | 15,349 | 8.8% |
| 6 | `secondary_property_type` | 12,571 | 7.2% |
| 7 | `primary_property_type` | 11,671 | 6.7% |
| 8 | `number_of_buildings` | 4,234 | 2.4% |
| 9 | `total_units` | 3,671 | 2.1% |
| 10 | `country` | 3,527 | 2.0% |
| 11 | `number_of_stories` | 2,981 | 1.7% |
| 12 | `site_complexity` | 2,078 | 1.2% |
| 13 | `pct_units_inspect` | 1,520 | 0.9% |
| 14 | `prior_report` | 0 | 0.0% |
