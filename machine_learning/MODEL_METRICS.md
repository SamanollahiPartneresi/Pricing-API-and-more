# PricePilot fee model — accuracy metrics

Hold-out (20% test split) accuracy for the deployed LightGBM fee model, vs. two
naive baselines. Lower error / higher "within X%" and R² is better.

- Source of truth (regenerated on every retrain): Keboola table `out.c-pricing_ml.fee_model_metrics`.
- Raw values: [`fee_model_metrics.csv`](./fee_model_metrics.csv).

_Auto-synced from Keboola on 2026-06-02 by `sync_model_artifacts.py`._

### Model vs. baselines (test set)

| Metric | model_test | baseline_predict_base_fee | baseline_predict_suggested_fee |
|---|---:|---:|---:|
| Rows | 28,296 | 28,296 | 28,296 |
| MAE | $523 | $734 | $899 |
| RMSE | $968 | $1,210 | $3,552 |
| Median error | 11.1% | 15.8% | 21.0% |
| Mean error (MAPE) | 22.7% | 33.8% | 45.7% |
| Within 10% | 46.0% | 32.6% | 26.6% |
| Within 20% | 73.1% | 59.1% | 49.2% |
| R² (dollars) | 0.481 | 0.189 | -5.987 |

### Train vs. test (overfit check)

| Metric | model_train | model_test |
|---|---:|---:|
| Rows | 113,180 | 28,296 |
| MAE | $482 | $523 |
| RMSE | $876 | $968 |
| Median error | 10.5% | 11.1% |
| Mean error (MAPE) | 20.3% | 22.7% |
| Within 10% | 48.2% | 46.0% |
| Within 20% | 75.1% | 73.1% |
| R² (dollars) | 0.580 | 0.481 |

### Per-service (test set)

| Metric | service_test::Debt PCA | service_test::Equity PCA | service_test::Phase I ESA |
|---|---:|---:|---:|
| Rows | 5,267 | 6,269 | 16,760 |
| MAE | $416 | $1,041 | $363 |
| RMSE | $690 | $1,742 | $546 |
| Median error | 9.3% | 16.6% | 10.4% |
| Mean error (MAPE) | 17.3% | 31.5% | 21.1% |
| Within 10% | 52.6% | 34.7% | 48.2% |
| Within 20% | 78.1% | 56.7% | 77.7% |
| R² (dollars) | 0.318 | 0.333 | 0.307 |
