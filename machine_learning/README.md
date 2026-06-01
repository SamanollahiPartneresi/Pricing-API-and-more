# PricePilot model training

This folder contains everything needed to rebuild PricePilot's fee-prediction model from scratch.

## Real-data model (current) — `train_fee_model.py`

Trains on **real historical quotes**, not synthetic data. Source table:
`pricing_fee_model_input` (Keboola bucket `Data_Science_Pricing_Agent`,
table id `in.c-Data_Science_Pricing_Agent.pricing_fee_model_input`) —
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

The synthetic pipeline below (`generate_training_data.py` / `train_model.py` /
`keboola_training_script.py`) predates the real data and is kept for reference.

---

## Why this exists

The model previously deployed to Keboola was trained on a dataset that has since been wiped
(the `pricing_training_data` table is now 100 empty rows). Worse, that dataset had labeled
`country_code` with US-city names like "Chicago" and "Dallas", so the deployed encoder ignored
real `US`/`CA` inputs and the model collapsed to a near-constant ≈ $7,500 prediction
regardless of building size, TAT, location, or service.

The new pipeline drives the canonical rule engine (`pricing_engine.calculate`) over a realistic
distribution of inputs and uses the rule-based price (plus small lognormal noise) as the target.
That gives a deterministic, reproducible baseline we can replace with real historical orders
once they're available.

## Files

| File | Purpose |
|------|---------|
| `pricing_factors_snapshot.csv` | Snapshot of `in.c-Pricing_Agent_Input_Data.pricing_factors` (257 rows, 4 services). Used by the local generator and emulator. |
| `generate_training_data.py` | Generates `training_data.csv` (~8k rows) by sampling inputs and computing rule-based totals. |
| `train_model.py` | Trains a `GradientBoostingRegressor` on `training_data.csv` and saves `pricepilot_model.pkl` + `training_metrics.json`. |
| `keboola_training_script.py` | Self-contained version that runs as a Keboola Python transformation. Reads `pricing_factors` from `/data/in/tables/`, generates data inline, trains, writes `pricepilot_model.pkl` to `/data/out/files/` tagged `pricepilot_model`. |

## Schema (kept in sync with `api.py` Flask inference)

**Numeric columns:**
`base_fee, tat, portfolio_size, building_area, land_area, limit_of_liability, number_of_stories, number_of_buildings, total_units, percent_units_to_inspect`

**Categorical columns (OneHotEncoder with `handle_unknown='ignore'`):**
`facility_type, secondary_property_type, travel_difficulty, prior_report, site_complexity, country_code`

The previous deployed model treated 8 of the numeric columns as categorical, which silenced their
signal entirely; this version restores them.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install pandas numpy scikit-learn joblib
python machine_learning/generate_training_data.py
python machine_learning/train_model.py
```

Expected test metrics on the synthetic set: **R² ≈ 0.96**, **MAE ≈ $200**, **MAPE ≈ 6%**.

## Run on Keboola

The script `keboola_training_script.py` is the body of the
`pricing_model_training_transformation` Python transformation. Trigger a run from the Keboola
UI or via `run_job`; the joblib file is tagged `pricepilot_model` so the Flask API picks it up
on next start (the API caches in-memory until restart).
