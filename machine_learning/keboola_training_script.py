"""
PricePilot model training transformation — self-contained.

Reads `pricing_factors` from the input mapping, inlines the canonical rule
engine (mirroring SL_Heaven's Pricing.main_algo), generates a synthetic
training dataset on-the-fly, and trains a GradientBoostingRegressor on the
correct numeric/categorical schema. Outputs `pricepilot_model.pkl` tagged
`pricepilot_model` for the PricePilot Flask API to consume.

Why synthetic data: the previous `pricing_training_data` table was empty
(100 blank rows) and the deployed model had been trained on a separate
dataset where `country_code` had been populated with US-city names. Anchoring
training on the production `pricing_factors` plus the rule engine gives a
deterministic, reproducible baseline.
"""

import math
import os
import random
import re

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    r2_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


# Inlined pricing engine (mirror of pricing_engine.py in the Pricing-API repo)

UNIT_INSPECTION_FACILITY_TYPE = {"Multi-Family", "Seniors Housing"}
NUMBER_OF_BUILDINGS_FACILITY_TYPE_1 = {"Multi-Family", "Seniors Housing", "Storage"}
NUMBER_OF_BUILDINGS_FACILITY_TYPE_2 = {
    "Industrial", "Healthcare", "Lodging", "Manufactured Housing", "Other",
    "Office", "Retail - Large", "Retail - Small", "Retail - Specialty",
    "Special Purpose",
}
NUMBER_OF_BUILDINGS_ELIGIBLE = (
    NUMBER_OF_BUILDINGS_FACILITY_TYPE_1 | NUMBER_OF_BUILDINGS_FACILITY_TYPE_2
)
SIZE_LEVEL_PRECEDENCE = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "2XL": 5, "3XL": 6}

FEE_KEYS = [
    "limit_of_liability_fee", "tat_fee", "portfolio_fee", "time_period_fee",
    "travel_difficulty_fee", "prior_report_fee", "site_complexity_fee",
    "international_fee", "units_fee", "size_fee", "buildings_fee", "stories_fee",
]

SERVICE_BASE_FEES = {1: 4000.0, 2: 2200.0, 3: 2500.0, 4: 2400.0}


def factor_value_to_percentage(raw):
    s = str(raw).strip().lower()
    if s == "rfp":
        return "RFP"
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return "RFP"


def _leading_int(token):
    m = re.match(r"-?\d+", str(token).strip())
    return int(m.group(0)) if m else 0


def parse_range_bounds(s):
    s = str(s or "").strip()
    if not s:
        return None
    if "to" in s:
        lo_s, hi_s = s.split("to", 1)
        return _leading_int(lo_s), float(_leading_int(hi_s))
    if "+" in s:
        return _leading_int(s.split("+", 1)[0]), float("inf")
    n = _leading_int(s)
    return n, float(n)


def parse_liability_range_bounds(s):
    s = str(s or "").strip().replace(",", "")
    if not s:
        return None
    lower = s.lower()
    m = re.search(r"less than or equal to (\d+)", lower)
    if m:
        return 0, float(int(m.group(1)))
    m = re.search(r"between (\d+) and (\d+)", lower)
    if m:
        return int(m.group(1)), float(int(m.group(2)))
    m = re.search(r"(\d+) or greater", lower) or re.search(r"greater than or equal to (\d+)", lower)
    if m:
        return int(m.group(1)), float("inf")
    return None


def factors_for(df, category):
    return df[df["category"] == category]


def _percent(row):
    return factor_value_to_percentage(row["value"])


def resolve_turnaround_time(df, tat):
    cat = factors_for(df, "Turnaround Time").copy()
    if cat.empty or tat <= 0:
        return None
    cat["_lvl"] = pd.to_numeric(cat["level"], errors="coerce")
    cat = cat.sort_values("_lvl")
    chosen = None
    for _, row in cat.iterrows():
        desc = str(row["description"]).lower()
        digits = "".join(c for c in desc if c.isdigit())
        day_value = int(digits) if digits else 0
        if "greater" in desc and "equal" in desc:
            if tat >= day_value:
                chosen = row
                break
        elif "less" in desc or "equal" in desc:
            if tat <= day_value:
                chosen = row
                break
        elif tat == day_value:
            chosen = row
            break
    if chosen is None:
        last = cat.iloc[-1]
        last_digits = "".join(c for c in str(last["description"]) if c.isdigit())
        last_day = int(last_digits) if last_digits else 0
        if tat > last_day:
            chosen = last
        else:
            return None
    return _percent(chosen)


def resolve_simple_range(df, category, val):
    cat = factors_for(df, category).copy()
    if cat.empty:
        return None
    cat["_lvl"] = pd.to_numeric(cat["level"], errors="coerce")
    cat = cat.sort_values("_lvl")
    for _, row in cat.iterrows():
        b = parse_range_bounds(row["description"])
        if b is None:
            continue
        lo, hi = b
        if lo <= val <= hi:
            return _percent(row)
    return None


def resolve_limit_of_liability(df, liability):
    cat = factors_for(df, "Limit of Liability").copy()
    if cat.empty:
        return None
    cat["_lvl"] = pd.to_numeric(cat["level"], errors="coerce")
    cat = cat.sort_values("_lvl")
    for _, row in cat.iterrows():
        b = parse_liability_range_bounds(row["description"])
        if b is None:
            continue
        lo, hi = b
        if lo <= liability <= hi:
            return _percent(row)
    return None


def _compare(value, op, threshold):
    return {
        "<=": value <= threshold, ">=": value >= threshold,
        "<": value < threshold,   ">": value > threshold, "=": value == threshold,
    }.get(op, False)


def matches_one_size_condition(condition, building_sf, land_ac):
    c = condition.strip()
    cl = c.lower()
    if "land ac" in cl:
        rest = re.sub(r"land\s+ac", "", c, flags=re.IGNORECASE).strip()
        variable_value = land_ac
    elif "building sf" in cl:
        rest = re.sub(r"building\s+sf", "", c, flags=re.IGNORECASE).strip()
        variable_value = building_sf
    else:
        return False
    m = re.match(r"([<>=]+)\s*([\d,]+)", rest)
    if not m:
        return False
    op = m.group(1).strip()
    threshold = float(m.group(2).replace(",", ""))
    return _compare(variable_value, op, threshold)


def matches_size_condition(description, building_sf, land_ac):
    condition_text = description.split(":", 1)[-1].strip()
    parts = re.split(r"\s+or\s+", condition_text, flags=re.IGNORECASE)
    for part in parts:
        if matches_one_size_condition(part, building_sf, land_ac):
            return True
    return False


def resolve_size(df, building_area, land_area, facility_type, secondary):
    if facility_type in UNIT_INSPECTION_FACILITY_TYPE:
        return None
    cat = factors_for(df, "Size").copy()
    if cat.empty:
        return None
    def _sort_key(lvl):
        s = str(lvl)
        try:
            return (0, float(s))
        except ValueError:
            return (1, float(SIZE_LEVEL_PRECEDENCE.get(s.upper(), 999)))
    cat["_o"] = cat["level"].map(_sort_key)
    cat = cat.sort_values("_o")
    if str(secondary).strip() == "Vacant Land":
        vacant = cat[cat["description"].str.contains(
            "Applied to certain Primary Property Types", case=False, na=False)]
        if not vacant.empty:
            return _percent(vacant.iloc[0])
        return _percent(cat.iloc[0])
    for _, row in cat.iterrows():
        if matches_size_condition(str(row["description"]), building_area, land_area):
            return _percent(row)
    return None


def resolve_travel_difficulty(df, level):
    if level is None or level == 0:
        return None
    cat = factors_for(df, "Travel Difficulty")
    rows = cat[cat["level"].astype(str) == str(level)]
    if rows.empty:
        return None
    return _percent(rows.iloc[0])


def resolve_by_description(df, category, description):
    if not description:
        return None
    cat = factors_for(df, category)
    if cat.empty:
        return None
    matches = cat[cat["description"].str.lower() == str(description).lower()]
    if matches.empty:
        return None
    return _percent(matches.iloc[0])


def resolve_time_period(df):
    cat = factors_for(df, "Time Period")
    if cat.empty:
        return None
    rows = cat[cat["level"].astype(str) == "1"]
    if rows.empty:
        return None
    return _percent(rows.iloc[0])


def resolve_units(df, facility_type, total_units, percent_to_inspect):
    if facility_type not in UNIT_INSPECTION_FACILITY_TYPE:
        return None
    if total_units <= 0 or percent_to_inspect <= 0:
        return None
    units = int(total_units * (percent_to_inspect / 100.0))
    return resolve_simple_range(df, "Unit Inspection", units)


def resolve_buildings(df, facility_type, n):
    if n <= 0:
        return None
    if facility_type in NUMBER_OF_BUILDINGS_FACILITY_TYPE_1:
        category = "# of Buildings 1"
    elif facility_type in NUMBER_OF_BUILDINGS_FACILITY_TYPE_2:
        category = "# of Buildings 2"
    else:
        return None
    if factors_for(df, category).empty:
        category = "# of Buildings"
        if factors_for(df, category).empty:
            return None
    return resolve_simple_range(df, category, n)


def fee_amount(pct, base_fee, fallback_rfp):
    if pct is None:
        return "RFP" if fallback_rfp else 0
    if pct == "RFP":
        return "RFP"
    return int(round(base_fee * float(pct)))


def rule_total(
    df, *, base_fee, tat, portfolio_size, building_area, land_area,
    facility_type, secondary_property_type, limit_of_liability,
    travel_difficulty_level, prior_report, site_complexity, country_code,
    number_of_stories, number_of_buildings, total_units, percent_units_to_inspect,
):
    if facility_type.strip().lower() == "special purpose":
        return None

    fees = {
        "limit_of_liability_fee": fee_amount(
            resolve_limit_of_liability(df, limit_of_liability),
            base_fee, limit_of_liability > 0),
        "tat_fee": fee_amount(resolve_turnaround_time(df, tat), base_fee, tat > 0),
        "portfolio_fee": fee_amount(
            resolve_simple_range(df, "Portfolio Size", portfolio_size) if portfolio_size > 0 else None,
            base_fee, portfolio_size > 0),
        "time_period_fee": fee_amount(resolve_time_period(df), base_fee, False),
        "travel_difficulty_fee": fee_amount(
            resolve_travel_difficulty(df, travel_difficulty_level),
            base_fee, (travel_difficulty_level or 0) > 0),
        "prior_report_fee": fee_amount(
            resolve_by_description(df, "Prior Report", prior_report),
            base_fee, bool(prior_report)),
        "site_complexity_fee": fee_amount(
            resolve_by_description(df, "Site Complexity", site_complexity),
            base_fee, bool(site_complexity)),
        "international_fee": fee_amount(
            resolve_by_description(df, "International", country_code),
            base_fee, bool(country_code)),
        "units_fee": fee_amount(
            resolve_units(df, facility_type, total_units, percent_units_to_inspect),
            base_fee, (facility_type in UNIT_INSPECTION_FACILITY_TYPE
                       and total_units > 0 and percent_units_to_inspect > 0)),
        "size_fee": fee_amount(
            resolve_size(df, building_area, land_area, facility_type, secondary_property_type),
            base_fee, (facility_type not in UNIT_INSPECTION_FACILITY_TYPE
                       and (building_area > 0 or land_area > 0))),
        "buildings_fee": fee_amount(
            resolve_buildings(df, facility_type, number_of_buildings),
            base_fee, (facility_type in NUMBER_OF_BUILDINGS_ELIGIBLE
                       and number_of_buildings > 0)),
        "stories_fee": fee_amount(
            resolve_simple_range(df, "# of Stories", number_of_stories) if number_of_stories > 0 else None,
            base_fee, number_of_stories > 0),
    }
    if any(v == "RFP" for v in fees.values()):
        return None
    subtotal = base_fee + sum(v for v in fees.values() if isinstance(v, int))
    return int(math.ceil(subtotal / 50.0) * 50)


# Synthetic training data generator

SERVICE_WEIGHTS = {4: 0.40, 1: 0.30, 2: 0.25, 3: 0.05}
FACILITY_WEIGHTS = {
    "Multi-Family": 0.28, "Office": 0.18, "Industrial": 0.15,
    "Retail - Large": 0.07, "Retail - Small": 0.05, "Retail - Specialty": 0.03,
    "Lodging": 0.06, "Healthcare": 0.04, "Seniors Housing": 0.05,
    "Manufactured Housing": 0.02, "Storage": 0.03, "Special Purpose": 0.02, "Other": 0.02,
}


def weighted_choice(rng, weights):
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def lookup_description(factors_df, service_id, category, level):
    rows = factors_df[
        (factors_df["order_form_service_id"] == service_id)
        & (factors_df["category"] == category)
        & (factors_df["level"].astype(str) == str(level))
    ]
    if rows.empty:
        rows = factors_df[
            (factors_df["order_form_service_id"] == service_id)
            & (factors_df["category"] == category)
        ]
        if rows.empty:
            return ""
    return str(rows.iloc[0]["description"])


def generate_training_data(factors_df, n_rows=8000, seed=42):
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    out = []
    attempts = 0
    rfp_dropped = 0
    while len(out) < n_rows and attempts < n_rows * 6:
        attempts += 1
        service_id = weighted_choice(rng, SERVICE_WEIGHTS)
        if service_id == 3:
            facility_type = rng.choice(["Office", "Industrial", "Retail - Large", "Multi-Family", "Other"])
        else:
            facility_type = weighted_choice(rng, FACILITY_WEIGHTS)

        base_fee = float(SERVICE_BASE_FEES.get(service_id, 4000.0))

        if facility_type in UNIT_INSPECTION_FACILITY_TYPE:
            total_units = int(np_rng.integers(1, 400))
            pct = int(rng.choice([10, 15, 20, 25, 50, 100]))
            building_area, land_area = 0.0, 0.0
        else:
            total_units, pct = 0, 0
            building_area = float(np.exp(np_rng.uniform(np.log(2_000), np.log(1_500_000))))
            land_area = float(np_rng.uniform(0.5, 60.0)) if rng.random() < 0.4 else 0.0

        if service_id == 3:
            tat = int(rng.choice([3, 5, 7, 10, 12, 15, 18, 20, 25, 30]))
        else:
            tat = int(rng.choice([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]))

        portfolio_size = int(rng.choices([1, 2, 4, 8, 15, 30, 60, 100], weights=[35, 15, 12, 12, 10, 8, 5, 3])[0])
        if service_id == 3:
            n_buildings = int(rng.choices([1, 2, 3, 4, 6, 8], weights=[40, 25, 15, 10, 6, 4])[0])
        elif facility_type in {"Multi-Family", "Seniors Housing", "Storage"}:
            n_buildings = int(rng.choices([1, 2, 3, 4, 6, 9, 12], weights=[35, 20, 15, 10, 10, 6, 4])[0])
        else:
            n_buildings = int(rng.choices([1, 2, 3, 5, 8], weights=[55, 20, 12, 8, 5])[0])
        n_stories = int(rng.choices([1, 2, 3, 5, 8, 12, 15], weights=[35, 25, 15, 12, 7, 4, 2])[0])

        country_code = "US" if rng.random() < 0.90 else "CA"
        limit = float(rng.choices(
            [50_000, 200_000, 400_000, 800_000, 1_500_000, 3_000_000, 5_000_000, 10_000_000],
            weights=[15, 25, 15, 15, 10, 10, 5, 5])[0])

        travel_level = int(rng.choices([1, 2, 3, 4, 5], weights=[55, 20, 12, 8, 5])[0])
        site_level = int(rng.choices([1, 2, 3], weights=[25, 60, 15])[0])
        prior_level = int(rng.choices([1, 2, 3, 4, 5], weights=[40, 20, 15, 15, 10])[0])
        secondary = "Vacant Land" if rng.random() < 0.04 else ""

        svc_factors = factors_df[factors_df["order_form_service_id"] == service_id]
        travel_desc = lookup_description(factors_df, service_id, "Travel Difficulty", travel_level)
        site_desc = lookup_description(factors_df, service_id, "Site Complexity", site_level)
        prior_desc = lookup_description(factors_df, service_id, "Prior Report", prior_level)

        try:
            total = rule_total(
                svc_factors,
                base_fee=base_fee, tat=tat, portfolio_size=float(portfolio_size),
                building_area=building_area, land_area=land_area,
                facility_type=facility_type, secondary_property_type=secondary,
                limit_of_liability=limit, travel_difficulty_level=travel_level,
                prior_report=prior_desc, site_complexity=site_desc, country_code=country_code,
                number_of_stories=float(n_stories), number_of_buildings=float(n_buildings),
                total_units=float(total_units), percent_units_to_inspect=float(pct),
            )
        except Exception:
            continue
        if total is None:
            rfp_dropped += 1
            continue

        noisy = total * float(np.exp(np_rng.normal(loc=0.0, scale=0.07)))
        noisy = max(noisy, base_fee * 0.5)

        out.append({
            "order_form_service_id": service_id, "base_fee": base_fee, "tat": tat,
            "portfolio_size": portfolio_size, "building_area": round(building_area, 1),
            "land_area": round(land_area, 1), "facility_type": facility_type,
            "secondary_property_type": secondary, "limit_of_liability": limit,
            "travel_difficulty": travel_desc, "prior_report": prior_desc,
            "site_complexity": site_desc, "country_code": country_code,
            "number_of_stories": n_stories, "number_of_buildings": n_buildings,
            "total_units": total_units, "percent_units_to_inspect": pct,
            "rule_based_total_fee": total, "total_fee": round(noisy, 2),
        })

    print(f"Generated {len(out)} rows. Dropped {rfp_dropped} RFP rows. Attempts: {attempts}")
    return pd.DataFrame(out)


# Model schema + training

NUMERIC_COLUMNS = [
    "base_fee", "tat", "portfolio_size", "building_area", "land_area",
    "limit_of_liability", "number_of_stories", "number_of_buildings",
    "total_units", "percent_units_to_inspect",
]
CATEGORICAL_COLUMNS = [
    "facility_type", "secondary_property_type", "travel_difficulty",
    "prior_report", "site_complexity", "country_code",
]
INPUT_COLUMNS = NUMERIC_COLUMNS + CATEGORICAL_COLUMNS


def prep_features(df):
    p = df.copy()
    for c in NUMERIC_COLUMNS:
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0).astype(float)
    for c in CATEGORICAL_COLUMNS:
        p[c] = p[c].fillna("").astype(str)
    return p[INPUT_COLUMNS]


def main():
    factors_path = os.environ.get("KBC_FACTORS_PATH", "/data/in/tables/pricing_factors")
    out_dir = os.environ.get("KBC_OUT_FILES_DIR", "/data/out/files")
    factors_df = pd.read_csv(factors_path)
    factors_df["order_form_service_id"] = pd.to_numeric(
        factors_df["order_form_service_id"], errors="coerce").astype("Int64")
    print(f"Loaded {len(factors_df)} pricing_factors rows from {factors_path}")

    df = generate_training_data(factors_df, n_rows=8000, seed=42)
    print("total_fee stats:", df["total_fee"].describe().round(1).to_dict())

    X = prep_features(df)
    y = pd.to_numeric(df["total_fee"], errors="coerce")
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.20, random_state=42)

    pipeline = Pipeline([
        ("prep", ColumnTransformer(
            transformers=[("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_COLUMNS)],
            remainder="passthrough", verbose_feature_names_out=True)),
        ("model", GradientBoostingRegressor(
            n_estimators=400, learning_rate=0.05, max_depth=5,
            subsample=0.85, min_samples_leaf=10, random_state=42)),
    ])
    pipeline.fit(X_train, y_train)

    for label, X_, y_ in [("train", X_train, y_train), ("test", X_test, y_test)]:
        preds = pipeline.predict(X_)
        print(
            f"[{label}] n={len(y_)} "
            f"MAE=${mean_absolute_error(y_, preds):.0f} "
            f"MAPE={mean_absolute_percentage_error(y_, preds)*100:.2f}% "
            f"R2={r2_score(y_, preds):.4f}"
        )

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "pricepilot_model.pkl")
    joblib.dump(pipeline, out_path)
    print(f"MODEL SAVED to {out_path}")


main()
