# PricePilot model training

This folder contains everything needed to rebuild PricePilot's fee-prediction model from scratch.

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
