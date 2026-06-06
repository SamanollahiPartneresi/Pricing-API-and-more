# PricePilot fee model — accuracy metrics

Hold-out (20% test split) accuracy for the deployed LightGBM fee model, vs. two
naive baselines. Lower error / higher "within X%" and R² is better.

- Source of truth (regenerated on every retrain): Keboola table `out.c-pricing_ml.fee_model_metrics`.
- Raw values: [`fee_model_metrics.csv`](./fee_model_metrics.csv).

_Auto-synced from Keboola on 2026-06-06 by `sync_model_artifacts.py`._

### Model vs. baselines (test set)

| Metric | model_test | baseline_predict_base_fee | baseline_predict_suggested_fee |
|---|---:|---:|---:|
| Rows | 28,296 | 28,296 | 28,296 |
| MAE | $530 | $734 | $899 |
| RMSE | $973 | $1,210 | $3,552 |
| Median error | 11.3% | 15.8% | 21.0% |
| Mean error (MAPE) | 23.0% | 33.8% | 45.7% |
| Within 10% | 45.5% | 32.6% | 26.6% |
| Within 20% | 72.5% | 59.1% | 49.2% |
| R² (dollars) | 0.475 | 0.189 | -5.987 |

### Train vs. test (overfit check)

| Metric | model_train | model_test |
|---|---:|---:|
| Rows | 113,180 | 28,296 |
| MAE | $490 | $530 |
| RMSE | $888 | $973 |
| Median error | 10.7% | 11.3% |
| Mean error (MAPE) | 20.7% | 23.0% |
| Within 10% | 47.4% | 45.5% |
| Within 20% | 74.3% | 72.5% |
| R² (dollars) | 0.568 | 0.475 |

### Per-service (test set)

| Metric | service_test::Debt PCA | service_test::Equity PCA | service_test::Phase I ESA |
|---|---:|---:|---:|
| Rows | 5,267 | 6,269 | 16,760 |
| MAE | $423 | $1,055 | $367 |
| RMSE | $698 | $1,749 | $549 |
| Median error | 9.3% | 17.1% | 10.6% |
| Mean error (MAPE) | 17.6% | 31.9% | 21.3% |
| Within 10% | 52.6% | 33.4% | 47.8% |
| Within 20% | 77.3% | 55.8% | 77.3% |
| R² (dollars) | 0.302 | 0.328 | 0.299 |
