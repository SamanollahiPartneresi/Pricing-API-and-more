"""
PricePilot fee model trainer — trained on REAL historical quotes.

Source table: `pricing_fee_model_input` (Keboola bucket
`pricing-data-transformation`), the cleaned/winsorized quote dataset
(~141k rows: Phase I ESA, Equity PCA, Debt PCA). Target is `log_fee`
(= natural log of the awarded scope fee); predictions are exponentiated
back to dollars for reporting.

Model: LightGBM gradient-boosted trees with NATIVE categorical handling
(no one-hot blow-up) and native NaN handling — a good fit for the many
sparse, high-cardinality string columns in this data.

Leakage discipline (critical): cost/margin columns, the final project
fee, project/scope status, and raw identifiers are EXCLUDED from the
feature set even though they live in the table. They are only known
after the project is awarded/closed, so training on them would inflate
offline scores and fail in production. `suggested_fee_ref` is kept — it
is a quote-time reference (the system's own suggestion), not an outcome.

Run modes:
  * Keboola Python transformation — map `pricing_fee_model_input` as an
    input table; it lands at /data/in/tables/pricing_fee_model_input.csv.
    Model + metrics are written to /data/out/files.
  * Local — set FEE_INPUT_PATH to a CSV export of the table, or pass the
    path as argv[1]. Outputs go next to this script.
"""

import json
import os
import sys

import joblib
import numpy as np
import pandas as pd

try:
    from lightgbm import LGBMRegressor
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "lightgbm is required. Install with `pip install lightgbm` "
        "or add it to the Keboola transformation packages."
    ) from exc

from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

TARGET_LOG = "log_fee"   # model target (natural log of fee)
TARGET_RAW = "fee"       # dollars, used only for reporting/baselines

# Feature set is deliberately RULE-ALIGNED: it mirrors the inputs the canonical
# rule engine uses (the same levers Streamlit / the API / the agent collect at
# quote time). This keeps the ML model deployable on a fresh quote and directly
# comparable to the rule-based total, side by side.
#
# turn_around_time is parsed to a numeric day count (rule engine treats TAT
# numerically); see prepare_features.
# One feature per rule-based lever — no redundant/derived duplicates.
# `created_month` is the single "Time Period / busy level" signal (it already
# encodes quarter and busy-season, so created_quarter/busy_season_flag are
# dropped). `land_area` (sqft) is dropped as a duplicate of `land_acreage`
# (acres) — the latter is the dimension the rule engine and the API collect.
# `base_fee` is intentionally EXCLUDED: it is a near-constant per service, so
# `service_type_id` already carries the same signal. A controlled experiment
# (logged in MODEL_LOG.md) confirmed dropping it leaves accuracy unchanged.
# It is still carried through the split as a quote-time BASELINE only
# (predict_base_fee), never as a model input.
NUMERIC_FEATURES = [
    "turn_around_time",
    "building_area",
    "land_acreage",
    "total_units",
    "pct_units_inspect",
    "number_of_stories",
    "number_of_buildings",
    "created_month",      # single "Time Period / busy level" lever
]

# Rule-aligned categorical levers (LightGBM handles these natively).
# `limit_of_liability_tier` is intentionally excluded: the API can't supply it
# at quote time (it sends the raw $ amount, not the trained 1-6 tier), so
# training on it would create train/serve skew. Re-add once the tier<->level
# mapping is confirmed and derived server-side.
CATEGORICAL_FEATURES = [
    "service_type_id",
    "primary_property_type",
    "secondary_property_type",
    "prior_report",
    "site_complexity",
    "country",
    "customer_type",
]

FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# Columns present in the table but deliberately NOT used as features.
# Costs/margin/total fee/status are post-award => leakage; IDs are identifiers;
# suggested_fee_ref + org/state/client/etc. are not rule-based levers (and
# suggested_fee_ref is not known when quoting a fresh project). suggested_fee_ref
# is still read for a benchmark baseline, not as a feature.
LEAKAGE_EXCLUDED = [
    "Service_Cost_Total", "Service_Cost_Misc", "service_margin",
    "admin_cost", "lab_cost", "other_costs", "pa_travel_cost",
    "general_pa_cost", "total_costs", "project_total_fee_ref",
    "status", "scope_status_group", "project_status_group",
]
NON_RULE_EXCLUDED = [
    "suggested_fee_ref", "org_name", "plink2_org_name", "state",
    "international_region", "client_type", "Project_ClientType",
    "is_portfolio", "portfolio_status", "department", "purpose",
    "Client_Format", "ClientFormat_ProjectType", "service_summary",
    "Specialty_Type", "Responsibility",
]
ID_EXCLUDED = [
    "project_number", "project_id", "source_id", "proposal_id",
    "organization_id", "plink2_organization_id", "created_date",
    "service_type_name",  # 1:1 with service_type_id; keep the id only
]

MISSING_CATEGORY = "__missing__"
RANDOM_STATE = 42


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------

def resolve_input_path():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    env = os.environ.get("FEE_INPUT_PATH")
    if env:
        return env
    return "/data/in/tables/pricing_fee_model_input.csv"


def resolve_output_dir():
    env = os.environ.get("FEE_OUTPUT_DIR")
    if env:
        return env
    if os.path.isdir("/data/out/files"):
        return "/data/out/files"
    return os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Feature prep
# --------------------------------------------------------------------------

def prepare_features(df):
    """Return an X frame with numerics as float (NaN preserved for LightGBM)
    and categoricals as pandas 'category' dtype with an explicit missing level.
    """
    X = pd.DataFrame(index=df.index)

    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            X[col] = np.nan
        elif col == "turn_around_time":
            # TAT may be stored as "10", "10 business days", etc. — take the
            # leading integer (rule engine treats TAT numerically).
            digits = df[col].astype("string").str.extract(r"(\d+)")[0]
            X[col] = pd.to_numeric(digits, errors="coerce")
        else:
            X[col] = pd.to_numeric(df[col], errors="coerce")

    for col in CATEGORICAL_FEATURES:
        if col in df.columns:
            s = df[col].astype("string")
        else:
            s = pd.Series(pd.NA, index=df.index, dtype="string")
        s = s.fillna(MISSING_CATEGORY).replace("", MISSING_CATEGORY)
        X[col] = s.astype("category")

    return X[FEATURES]


def regression_report(y_true_dollars, y_pred_dollars):
    y_true = np.asarray(y_true_dollars, dtype=float)
    y_pred = np.clip(np.asarray(y_pred_dollars, dtype=float), 1.0, None)
    abs_pct = np.abs(y_pred - y_true) / np.clip(y_true, 1.0, None)
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "median_ape_pct": float(np.median(abs_pct) * 100.0),
        "mape_pct": float(np.mean(abs_pct) * 100.0),
        "within_10pct": float(np.mean(abs_pct <= 0.10) * 100.0),
        "within_20pct": float(np.mean(abs_pct <= 0.20) * 100.0),
        "r2_dollars": float(r2_score(y_true, y_pred)),
    }


# --------------------------------------------------------------------------
# Train
# --------------------------------------------------------------------------

def main():
    in_path = resolve_input_path()
    out_dir = resolve_output_dir()
    print(f"Reading: {in_path}")

    df = pd.read_csv(in_path, low_memory=False)
    print(f"Loaded {len(df):,} rows, {df.shape[1]} columns")

    # Target. Keep only rows with a usable fee/log_fee.
    df[TARGET_RAW] = pd.to_numeric(df.get(TARGET_RAW), errors="coerce")
    if TARGET_LOG in df.columns:
        df[TARGET_LOG] = pd.to_numeric(df[TARGET_LOG], errors="coerce")
    else:
        df[TARGET_LOG] = np.log(df[TARGET_RAW])
    df = df[(df[TARGET_RAW] > 0) & df[TARGET_LOG].notna()].copy()
    print(f"Usable rows after target filter: {len(df):,}")

    X = prepare_features(df)
    y_log = df[TARGET_LOG].to_numpy()
    y_dollars = df[TARGET_RAW].to_numpy()

    strat = df["service_type_id"].astype(str) if "service_type_id" in df.columns else None
    svc_series = df.get("service_type_name", df.get("service_type_id", pd.Series(index=df.index)))
    if "suggested_fee_ref" in df.columns:
        sugg_all = pd.to_numeric(df["suggested_fee_ref"], errors="coerce").to_numpy()
    else:
        sugg_all = np.full(len(df), np.nan)
    # base_fee is no longer a model feature, but we still carry it through the
    # split to score the predict_base_fee baseline on the same test rows.
    base_fee_all = (
        pd.to_numeric(df["base_fee"], errors="coerce").fillna(0).to_numpy()
        if "base_fee" in df.columns else np.zeros(len(df))
    )

    (X_tr, X_te, ylog_tr, ylog_te, yd_tr, yd_te,
     svc_tr, svc_te, _sugg_tr, sugg_te, _base_tr, base_fee_te) = train_test_split(
        X, y_log, y_dollars, svc_series, sugg_all, base_fee_all,
        test_size=0.20, random_state=RANDOM_STATE, stratify=strat,
    )

    cat_idx = [FEATURES.index(c) for c in CATEGORICAL_FEATURES]
    model = LGBMRegressor(
        objective="regression",
        n_estimators=1200,
        learning_rate=0.03,
        num_leaves=64,
        min_child_samples=40,
        subsample=0.85,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        random_state=RANDOM_STATE,
        n_jobs=int(os.environ.get("FEE_N_JOBS", "4")),
        verbose=-1,
    )
    model.fit(
        X_tr, ylog_tr,
        categorical_feature=cat_idx,
    )

    pred_te = np.exp(model.predict(X_te))
    pred_tr = np.exp(model.predict(X_tr))

    metrics = {
        "rows_total": int(len(df)),
        "rows_train": int(len(X_tr)),
        "rows_test": int(len(X_te)),
        "features": FEATURES,
        "leakage_excluded": LEAKAGE_EXCLUDED,
        "non_rule_excluded": NON_RULE_EXCLUDED,
        "model": {
            "train": regression_report(yd_tr, pred_tr),
            "test": regression_report(yd_te, pred_te),
        },
    }

    # Baselines on the SAME test set, so the model's lift is honest.
    # (base_fee and suggested_fee_ref are benchmarks only — NOT model features.)
    sugg_te = np.where(np.isnan(sugg_te) | (sugg_te <= 0), base_fee_te, sugg_te)
    metrics["baselines"] = {
        "predict_base_fee": regression_report(yd_te, base_fee_te),
        "predict_suggested_fee": regression_report(yd_te, sugg_te),
    }

    # Per-service test accuracy.
    per_service = {}
    svc_te_arr = np.asarray(svc_te).astype(str)
    for svc in pd.unique(svc_te_arr):
        mask = svc_te_arr == svc
        if mask.sum() >= 30:
            per_service[svc] = regression_report(yd_te[mask], pred_te[mask])
    metrics["per_service_test"] = per_service

    importance = (
        pd.DataFrame({"feature": FEATURES, "gain": model.booster_.feature_importance("gain")})
        .sort_values("gain", ascending=False)
        .reset_index(drop=True)
    )
    metrics["top_features"] = importance.head(20).to_dict(orient="records")

    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, "pricepilot_fee_model.pkl")
    joblib.dump(
        {
            "model": model,
            "features": FEATURES,
            "numeric_features": NUMERIC_FEATURES,
            "categorical_features": CATEGORICAL_FEATURES,
            "missing_category": MISSING_CATEGORY,
            "target": "log_fee",
            "target_transform": "exp",
        },
        model_path,
    )
    importance.to_csv(os.path.join(out_dir, "fee_model_feature_importance.csv"), index=False)
    with open(os.path.join(out_dir, "fee_model_metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)

    t = metrics["model"]["test"]
    print("\n=== TEST (dollars) ===")
    print(f"MAE        ${t['mae']:,.0f}")
    print(f"RMSE       ${t['rmse']:,.0f}")
    print(f"Median APE {t['median_ape_pct']:.1f}%   MAPE {t['mape_pct']:.1f}%")
    print(f"Within 10% {t['within_10pct']:.1f}%   within 20% {t['within_20pct']:.1f}%")
    print(f"R2         {t['r2_dollars']:.3f}")
    print("\n=== BASELINES (test) ===")
    for name, b in metrics["baselines"].items():
        print(f"{name:24s} MAE ${b['mae']:,.0f}  medAPE {b['median_ape_pct']:.1f}%")
    print("\n=== TOP FEATURES (gain) ===")
    for r in metrics["top_features"][:12]:
        print(f"{r['feature']:24s} {r['gain']:,.0f}")
    print(f"\nSaved model -> {model_path}")


if __name__ == "__main__":
    main()
