"""
Train PricePilot's fee-prediction model on `training_data.csv`.

Schema notes (fixing the previous deployed model):
  * Numeric columns are passed through to the regressor unchanged; they no
    longer get string-encoded as categoricals.
  * `country_code` is now a real 2-letter code (US/CA), not a US-city name.
  * Eight previously empty categoricals (`portfolio_size`, `land_area`,
    `limit_of_liability`, `total_units`, `percent_units_to_inspect`,
    `number_of_buildings`) are now numeric and contribute real signal.
  * `OneHotEncoder(handle_unknown='ignore')` still tolerates novel labels,
    but with a populated training set those labels are now learned.

Output: `machine_learning/pricepilot_model.pkl`
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ML_DIR = Path(__file__).resolve().parent
TRAINING_CSV = ML_DIR / "training_data.csv"
MODEL_PATH = ML_DIR / "pricepilot_model.pkl"
METRICS_PATH = ML_DIR / "training_metrics.json"

# Inference schema — keep in sync with `api.py` (Flask API)
NUMERIC_COLUMNS = [
    "base_fee",
    "tat",
    "portfolio_size",
    "building_area",
    "land_area",
    "limit_of_liability",
    "number_of_stories",
    "number_of_buildings",
    "total_units",
    "percent_units_to_inspect",
]
CATEGORICAL_COLUMNS = [
    "facility_type",
    "secondary_property_type",
    "travel_difficulty",
    "prior_report",
    "site_complexity",
    "country_code",
]
INPUT_COLUMNS = NUMERIC_COLUMNS + CATEGORICAL_COLUMNS
TARGET_COLUMN = "total_fee"


def prep_features(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce dtypes for both train and inference. Numeric → float; categorical → str."""
    prepared = df.copy()
    for col in NUMERIC_COLUMNS:
        prepared[col] = pd.to_numeric(prepared[col], errors="coerce").fillna(0).astype(float)
    for col in CATEGORICAL_COLUMNS:
        prepared[col] = prepared[col].fillna("").astype(str)
    return prepared[INPUT_COLUMNS]


def build_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_COLUMNS),
        ],
        remainder="passthrough",
        verbose_feature_names_out=True,
    )
    regressor = GradientBoostingRegressor(
        n_estimators=400,
        learning_rate=0.05,
        max_depth=5,
        subsample=0.85,
        min_samples_leaf=10,
        random_state=42,
    )
    return Pipeline([("prep", preprocessor), ("model", regressor)])


def evaluate(model: Pipeline, X: pd.DataFrame, y: pd.Series, label: str) -> dict:
    preds = model.predict(X)
    metrics = {
        "label": label,
        "n_samples": int(len(y)),
        "mae": float(mean_absolute_error(y, preds)),
        "mape": float(mean_absolute_percentage_error(y, preds)),
        "r2": float(r2_score(y, preds)),
        "mean_actual": float(np.mean(y)),
        "mean_predicted": float(np.mean(preds)),
    }
    print(
        f"[{label}] n={metrics['n_samples']}  "
        f"MAE=${metrics['mae']:.0f}  "
        f"MAPE={metrics['mape']*100:.2f}%  "
        f"R²={metrics['r2']:.4f}  "
        f"mean_actual=${metrics['mean_actual']:.0f}  "
        f"mean_predicted=${metrics['mean_predicted']:.0f}"
    )
    return metrics


def main() -> None:
    df = pd.read_csv(TRAINING_CSV)
    print(f"Loaded {len(df)} training rows from {TRAINING_CSV.name}")

    X = prep_features(df)
    y = pd.to_numeric(df[TARGET_COLUMN], errors="coerce")
    mask = y.notna()
    X = X.loc[mask]
    y = y.loc[mask]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42
    )

    pipeline = build_pipeline()
    pipeline.fit(X_train, y_train)

    train_metrics = evaluate(pipeline, X_train, y_train, label="train")
    test_metrics = evaluate(pipeline, X_test, y_test, label="test")

    joblib.dump(pipeline, MODEL_PATH)
    print(f"Saved model → {MODEL_PATH}")

    # Persist metrics alongside the model so we can audit drift over time.
    with METRICS_PATH.open("w") as fh:
        json.dump(
            {
                "train": train_metrics,
                "test": test_metrics,
                "numeric_columns": NUMERIC_COLUMNS,
                "categorical_columns": CATEGORICAL_COLUMNS,
                "model_class": type(pipeline.named_steps["model"]).__name__,
            },
            fh,
            indent=2,
        )
    print(f"Saved metrics → {METRICS_PATH}")


if __name__ == "__main__":
    main()
