# PricePilot model training

This folder contains everything needed to rebuild PricePilot's fee-prediction model from scratch.

## Real-data model (current) — `train_fee_model.py`

Trains on **real historical quotes**, not synthetic data. Source table:
`pricing_fee_model_input` (Keboola bucket `pricing-data-transformation`,
table id `in.c-pricing-data-transformation.pricing_fee_model_input`) —
~141k cleaned/winsorized rows across Phase I ESA, Equity PCA, and Debt PCA.

- **Target:** `log_fee` (natural log of the awarded scope fee); predictions
  are exponentiated back to dollars for reporting.
- **Model:** LightGBM with native categorical + NaN handling (no one-hot blow-up).
- **Leakage discipline:** cost/margin columns, final project fee, project/scope
  status, and raw identifiers are present in the table but **excluded** as
  features (they're only known after award). `suggested_fee_ref` is kept (it's a
  quote-time reference, not an outcome). See `LEAKAGE_EXCLUDED` / `ID_EXCLUDED`.
- **Outputs:** `pricepilot_fee_model.pkl` (model + feature schema bundle),
  `fee_model_metrics.json` (train/test/per-service metrics + baselines), and
  `fee_model_feature_importance.csv`.

Run as a **Keboola Python transformation**: map `pricing_fee_model_input` as an
input table (lands at `/data/in/tables/pricing_fee_model_input.csv`); outputs go
to `/data/out/files`. Add `lightgbm` to the transformation packages.

Run **locally** against a CSV export:

```bash
pip install lightgbm pandas numpy scikit-learn joblib
FEE_INPUT_PATH=/path/to/pricing_fee_model_input.csv python machine_learning/train_fee_model.py
```

> Note: on some WSL/sandbox environments LightGBM deadlocks with `n_jobs=-1`
> (OpenMP). The trainer defaults to `n_jobs=4`; override with `FEE_N_JOBS`.

## Keeping GitHub in sync with the deployed model

Each training run rewrites two small tables in Keboola Storage:
`out.c-pricing_ml.fee_model_importance` and `out.c-pricing_ml.fee_model_metrics`.
These are the **source of truth**. The committed snapshots in this folder are kept
up to date automatically:

| Committed file | Generated from |
|---|---|
| `fee_model_importance.csv` + `FEATURE_IMPORTANCE.md` | `out.c-pricing_ml.fee_model_importance` |
| `fee_model_metrics.csv` + `MODEL_METRICS.md` | `out.c-pricing_ml.fee_model_metrics` |

`sync_model_artifacts.py` pulls those tables and regenerates the files. It runs in
CI via `.github/workflows/sync-model-artifacts.yml`:

- **Daily** (cron) and **on demand** (Actions → "Run workflow").
- **Right after a retrain**, if you fire a `repository_dispatch` of type
  `model-retrained` (e.g. from an orchestration step):

  ```bash
  curl -X POST -H "Authorization: token <PAT>" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/SamanollahiPartneresi/Pricing-API-and-more/dispatches \
    -d '{"event_type":"model-retrained"}'
  ```

**One-time setup (required):** add a repository secret **`KBC_TOKEN`** (a Keboola
Storage API token that can read the `out.c-pricing_ml` bucket) under
GitHub → Settings → Secrets and variables → Actions. Optionally add `KBC_URL`
if your stack isn't `https://connection.keboola.com`.

Run it locally too:

```bash
KBC_TOKEN=... python machine_learning/sync_model_artifacts.py
```

---

## Files

| File | Purpose |
|------|---------|
| `train_fee_model.py` | Trains the production LightGBM fee model on real historical quotes; writes the `pricepilot_fee_model` bundle, metrics, and feature importance. |
| `keboola_fee_training_script.py` | Self-contained version of the trainer that runs as a Keboola Python transformation. |
| `sync_model_artifacts.py` | Pulls the latest metrics/importance tables from Keboola Storage and regenerates the committed `*.csv` / `*.md` snapshots. |
| `fee_model_metrics.csv` / `MODEL_METRICS.md` | Committed snapshot of the latest training metrics. |
| `fee_model_importance.csv` / `FEATURE_IMPORTANCE.md` | Committed snapshot of feature importance. |
| `fee_model_runs.csv` / `MODEL_LOG.md` | History of training runs. |

## Run on Keboola

`keboola_fee_training_script.py` is the body of the fee-model training Python
transformation. Map `pricing_fee_model_input` as an input table and add `lightgbm`
to the transformation packages. The resulting bundle is tagged `pricepilot_fee_model`
so the Flask API picks it up on next start (the API caches in-memory until restart).
