# PricePilot fee model — accuracy metrics

Hold-out (20% test split) accuracy for the deployed LightGBM fee model, vs. two
naive baselines. Lower error / higher "within X%" and R² is better.

- Source of truth (regenerated on every retrain): Keboola table `out.c-pricing_ml.fee_model_metrics`.
- Raw values: [`fee_model_metrics.csv`](./fee_model_metrics.csv).

_Auto-synced from Keboola on 2026-06-21 by `sync_model_artifacts.py`._

### Model vs. baselines (test set)

| Metric | model_test | baseline_predict_base_fee | baseline_predict_suggested_fee |
|---|---:|---:|---:|
| Rows | 28,732 | 28,732 | 28,732 |
| MAE | $497 | $745 | $902 |
| RMSE | $933 | $1,223 | $3,530 |
| Median error | 9.9% | 16.7% | 21.2% |
| Mean error (MAPE) | 21.0% | 34.1% | 45.0% |
| Within 10% | 50.2% | 32.1% | 27.0% |
| Within 20% | 75.6% | 58.4% | 48.8% |
| R² (dollars) | 0.523 | 0.181 | -5.824 |

### Train vs. test (overfit check)

| Metric | model_train | model_test |
|---|---:|---:|
| Rows | 114,925 | 28,732 |
| MAE | $444 | $497 |
| RMSE | $837 | $933 |
| Median error | 9.2% | 9.9% |
| Mean error (MAPE) | 18.4% | 21.0% |
| Within 10% | 53.1% | 50.2% |
| Within 20% | 78.8% | 75.6% |
| R² (dollars) | 0.621 | 0.523 |

### Per-service (test set)

| Metric | service_test::Debt PCA | service_test::Equity PCA | service_test::Phase I ESA |
|---|---:|---:|---:|
| Rows | 5,351 | 6,369 | 17,012 |
| MAE | $397 | $1,014 | $335 |
| RMSE | $662 | $1,687 | $516 |
| Median error | 8.5% | 16.2% | 9.1% |
| Mean error (MAPE) | 15.5% | 30.7% | 19.1% |
| Within 10% | 55.9% | 35.7% | 53.9% |
| Within 20% | 80.4% | 57.4% | 81.0% |
| R² (dollars) | 0.391 | 0.387 | 0.381 |
