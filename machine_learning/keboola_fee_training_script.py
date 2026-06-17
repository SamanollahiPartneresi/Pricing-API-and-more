"""
PricePilot fee model — Keboola Python transformation (REAL data).

Body of the `pricepilot_fee_model_training` transformation. Reads the cleaned
historical-quote table `pricing_fee_model_input` from the input mapping, trains
a LightGBM regressor on `log_fee` using a RULE-ALIGNED feature set (the same
levers the rule engine / Streamlit / API collect), and writes:

  /data/out/files/pricepilot_fee_model.pkl   (model + schema, tagged)
  /data/out/tables/fee_model_metrics.csv      (queryable metrics + baselines)
  /data/out/tables/fee_model_importance.csv   (gain-based feature importance)

Packages: lightgbm, pandas, numpy, scikit-learn, joblib.
"""

import os
import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

TARGET_LOG = "log_fee"
TARGET_RAW = "fee"
RANDOM_STATE = 42
MISSING_CATEGORY = "__missing__"

# Rule-aligned features (mirror the rule engine's levers; deployable on a fresh
# quote). turn_around_time is parsed to a numeric day count. One feature per
# lever: created_month is the single "Time Period / busy level" signal (drops
# the redundant created_quarter/busy_season_flag), land_area (sqft) is dropped
# as a duplicate of land_acreage, and limit_of_liability_tier is excluded
# because the API can't supply the trained tier at quote time (train/serve skew).
# base_fee is also EXCLUDED: near-constant per service, so service_type_id
# carries the same signal (a logged experiment showed no accuracy change). It is
# still carried through the split for the predict_base_fee baseline only.
NUMERIC_FEATURES = [
    "turn_around_time", "building_area", "land_acreage",
    "total_units", "pct_units_inspect", "number_of_stories", "number_of_buildings",
    "created_month",
]
CATEGORICAL_FEATURES = [
    "service_type_id", "primary_property_type", "secondary_property_type",
    "prior_report", "site_complexity", "country", "customer_type",
]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def prepare_features(df):
    X = pd.DataFrame(index=df.index)
    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            X[col] = np.nan
        elif col == "turn_around_time":
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


def regression_report(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.clip(np.asarray(y_pred, dtype=float), 1.0, None)
    ape = np.abs(y_pred - y_true) / np.clip(y_true, 1.0, None)
    return {
        "n": int(len(y_true)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "median_ape_pct": float(np.median(ape) * 100.0),
        "mape_pct": float(np.mean(ape) * 100.0),
        "within_10pct": float(np.mean(ape <= 0.10) * 100.0),
        "within_20pct": float(np.mean(ape <= 0.20) * 100.0),
        "r2_dollars": float(r2_score(y_true, y_pred)),
    }


df = pd.read_csv("/data/in/tables/pricing_fee_model_input", low_memory=False)
print(f"Loaded {len(df):,} rows, {df.shape[1]} columns")

df[TARGET_RAW] = pd.to_numeric(df.get(TARGET_RAW), errors="coerce")
if TARGET_LOG in df.columns:
    df[TARGET_LOG] = pd.to_numeric(df[TARGET_LOG], errors="coerce")
else:
    df[TARGET_LOG] = np.log(df[TARGET_RAW])
df = df[(df[TARGET_RAW] > 0) & df[TARGET_LOG].notna()].copy()
print(f"Usable rows: {len(df):,}")

X = prepare_features(df)
y_log = df[TARGET_LOG].to_numpy()
y_dollars = df[TARGET_RAW].to_numpy()

strat = df["service_type_id"].astype(str) if "service_type_id" in df.columns else None
svc_series = df.get("service_type_name", df.get("service_type_id", pd.Series(index=df.index)))
if "suggested_fee_ref" in df.columns:
    sugg_all = pd.to_numeric(df["suggested_fee_ref"], errors="coerce").to_numpy()
else:
    sugg_all = np.full(len(df), np.nan)
# base_fee is no longer a feature; carry it through the split for the baseline.
base_fee_all = (
    pd.to_numeric(df["base_fee"], errors="coerce").fillna(0).to_numpy()
    if "base_fee" in df.columns else np.zeros(len(df))
)

(X_tr, X_te, ylog_tr, ylog_te, yd_tr, yd_te,
 svc_tr, svc_te, _sugg_tr, sugg_te, _base_tr, base_te) = train_test_split(
    X, y_log, y_dollars, svc_series, sugg_all, base_fee_all,
    test_size=0.20, random_state=RANDOM_STATE, stratify=strat,
)

cat_idx = [FEATURES.index(c) for c in CATEGORICAL_FEATURES]
model = LGBMRegressor(
    objective="regression", n_estimators=1200, learning_rate=0.03,
    num_leaves=64, min_child_samples=40, subsample=0.85, subsample_freq=1,
    colsample_bytree=0.8, reg_lambda=1.0, random_state=RANDOM_STATE,
    n_jobs=4, verbose=-1,
)
model.fit(X_tr, ylog_tr, categorical_feature=cat_idx)
print("Model trained")

pred_te = np.exp(model.predict(X_te))
pred_tr = np.exp(model.predict(X_tr))

sugg_te = np.where(np.isnan(sugg_te) | (sugg_te <= 0), base_te, sugg_te)

rows = []
def add(scope, rep):
    for metric, value in rep.items():
        rows.append({"scope": scope, "metric": metric, "value": round(float(value), 4)})

add("model_train", regression_report(yd_tr, pred_tr))
add("model_test", regression_report(yd_te, pred_te))
add("baseline_predict_base_fee", regression_report(yd_te, base_te))
add("baseline_predict_suggested_fee", regression_report(yd_te, sugg_te))

svc_te_arr = np.asarray(svc_te).astype(str)
for svc in pd.unique(svc_te_arr):
    mask = svc_te_arr == svc
    if mask.sum() >= 30:
        add(f"service_test::{svc}", regression_report(yd_te[mask], pred_te[mask]))

metrics_df = pd.DataFrame(rows)
importance_df = (
    pd.DataFrame({"feature": FEATURES, "gain": model.booster_.feature_importance("gain")})
    .sort_values("gain", ascending=False).reset_index(drop=True)
)

os.makedirs("/data/out/files", exist_ok=True)
os.makedirs("/data/out/tables", exist_ok=True)
joblib.dump(
    {
        "model": model, "features": FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "missing_category": MISSING_CATEGORY,
        "target": "log_fee", "target_transform": "exp",
    },
    "/data/out/files/pricepilot_fee_model.pkl",
)
metrics_df.to_csv("/data/out/tables/fee_model_metrics.csv", index=False)
importance_df.to_csv("/data/out/tables/fee_model_importance.csv", index=False)

t = metrics_df[metrics_df["scope"] == "model_test"].set_index("metric")["value"].to_dict()
print(f"TEST  MAE=${t.get('mae'):,.0f}  medAPE={t.get('median_ape_pct')}%  R2={t.get('r2_dollars')}")
print("Saved model + metrics + importance")
