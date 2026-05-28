"""
Synthetic training-data generator for PricePilot.

Approach: drive the canonical `pricing_engine.calculate()` over a realistic
distribution of inputs (service, facility_type, area, TAT, etc.), record the
inputs as the ML features, and use the rule-based total fee as the regression
target. A small lognormal noise factor is then applied to simulate market /
human pricing variation, so the ML model learns a smoothed version of the
deterministic rule instead of memorizing it.

Why synthetic? The Keboola `pricing_training_data` table is currently empty
and the model previously deployed was trained on a different (and since-wiped)
dataset that mislabeled `country_code` as US-city names, leaving the model
near-constant. Synthetic data anchored on the production `pricing_factors`
gives us a faithful, reproducible baseline that we can replace with real
historical orders once they become available.

Output: `machine_learning/training_data.csv`
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pricing_engine import (  # noqa: E402
    PRIMARY_PROPERTY_TYPES,
    SERVICE_BASE_FEES,
    UNIT_INSPECTION_FACILITY_TYPE,
    calculate,
)

SNAPSHOT_PATH = Path(__file__).resolve().parent / "pricing_factors_snapshot.csv"
OUTPUT_PATH = Path(__file__).resolve().parent / "training_data.csv"

ML_INPUT_COLUMNS = [
    "order_form_service_id",
    "base_fee",
    "tat",
    "portfolio_size",
    "building_area",
    "land_area",
    "facility_type",
    "secondary_property_type",
    "limit_of_liability",
    "travel_difficulty",
    "prior_report",
    "site_complexity",
    "country_code",
    "number_of_stories",
    "number_of_buildings",
    "total_units",
    "percent_units_to_inspect",
    "total_fee",
]

SERVICE_WEIGHTS = {
    4: 0.40,  # PCA Debt — most common in practice
    1: 0.30,  # PCA Equity
    2: 0.25,  # ESA
    3: 0.05,  # Zoning (rare; algorithm-eligible factors only)
}

# Skewed toward common values; rarer extremes still represented.
FACILITY_WEIGHTS = {
    "Multi-Family": 0.28,
    "Office": 0.18,
    "Industrial": 0.15,
    "Retail - Large": 0.07,
    "Retail - Small": 0.05,
    "Retail - Specialty": 0.03,
    "Lodging": 0.06,
    "Healthcare": 0.04,
    "Seniors Housing": 0.05,
    "Manufactured Housing": 0.02,
    "Storage": 0.03,
    "Special Purpose": 0.02,
    "Other": 0.02,
}
assert abs(sum(FACILITY_WEIGHTS.values()) - 1.0) < 1e-6


def weighted_choice(rng: random.Random, weights: dict) -> object:
    keys = list(weights.keys())
    probs = [weights[k] for k in keys]
    return rng.choices(keys, weights=probs, k=1)[0]


def sample_input(rng: random.Random, np_rng: np.random.Generator, service_id: int) -> dict:
    """Generate one realistic input row for the requested service."""
    facility_type = weighted_choice(rng, FACILITY_WEIGHTS)
    if service_id == 3:
        # Zoning is most commonly used for commercial sites.
        facility_type = rng.choice(
            ["Office", "Industrial", "Retail - Large", "Multi-Family", "Other"]
        )

    base_fee = float(SERVICE_BASE_FEES.get(service_id, 4000.0))

    if facility_type in UNIT_INSPECTION_FACILITY_TYPE:
        total_units = int(np_rng.integers(1, 400))
        percent_units_to_inspect = int(rng.choice([10, 15, 20, 25, 50, 100]))
        building_area = 0.0
        land_area = 0.0
    else:
        total_units = 0
        percent_units_to_inspect = 0
        building_area = float(np.exp(np_rng.uniform(np.log(2_000), np.log(1_500_000))))
        land_area = float(np_rng.uniform(0.5, 60.0)) if rng.random() < 0.4 else 0.0

    if service_id == 3:
        tat = int(rng.choice([3, 5, 7, 10, 12, 15, 18, 20, 25, 30]))
    else:
        # Algorithm-eligible TAT values for services 1/2/4 are 10..20; rush
        # days (1..9) all map to RFP, which we want to exclude from training.
        tat = int(rng.choice([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]))

    portfolio_size = int(rng.choices([1, 2, 4, 8, 15, 30, 60, 100], weights=[35, 15, 12, 12, 10, 8, 5, 3])[0])

    if service_id == 3:
        n_buildings = int(rng.choices([1, 2, 3, 4, 6, 8], weights=[40, 25, 15, 10, 6, 4])[0])
    elif facility_type in {"Multi-Family", "Seniors Housing", "Storage"}:
        n_buildings = int(rng.choices([1, 2, 3, 4, 6, 9, 12], weights=[35, 20, 15, 10, 10, 6, 4])[0])
    else:
        n_buildings = int(rng.choices([1, 2, 3, 5, 8], weights=[55, 20, 12, 8, 5])[0])

    n_stories = int(rng.choices([1, 2, 3, 5, 8, 12, 15], weights=[35, 25, 15, 12, 7, 4, 2])[0])

    # 90% US, 10% CA. Note: per the production factor table, CA on PCA / ESA is RFP,
    # so most CA rows will be dropped after computing total_fee — that's fine.
    country_code = "US" if rng.random() < 0.90 else "CA"

    limit_of_liability = float(rng.choices(
        [50_000, 200_000, 400_000, 800_000, 1_500_000, 3_000_000, 5_000_000, 10_000_000],
        weights=[15, 25, 15, 15, 10, 10, 5, 5],
    )[0])

    travel_difficulty_level = int(rng.choices([1, 2, 3, 4, 5], weights=[55, 20, 12, 8, 5])[0])
    site_complexity_level = int(rng.choices([1, 2, 3], weights=[25, 60, 15])[0])
    prior_report_level = int(rng.choices([1, 2, 3, 4, 5], weights=[40, 20, 15, 15, 10])[0])

    secondary_property_type = ""
    if rng.random() < 0.04:
        secondary_property_type = "Vacant Land"

    return {
        "order_form_service_id": service_id,
        "facility_type": facility_type,
        "secondary_property_type": secondary_property_type,
        "base_fee": base_fee,
        "tat": tat,
        "portfolio_size": portfolio_size,
        "building_area": round(building_area, 1),
        "land_area": round(land_area, 1),
        "country_code": country_code,
        "limit_of_liability": limit_of_liability,
        "travel_difficulty_level": travel_difficulty_level,
        "site_complexity_level": site_complexity_level,
        "prior_report_level": prior_report_level,
        "number_of_buildings": n_buildings,
        "number_of_stories": n_stories,
        "total_units": total_units,
        "percent_units_to_inspect": percent_units_to_inspect,
    }


def lookup_description(
    factors_df: pd.DataFrame,
    service_id: int,
    category: str,
    level: int,
) -> str:
    """Return the description string for a (service, category, level) tuple,
    falling back to the first row of that category if the level is missing."""
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


def generate(n_rows: int, seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    factors_df = pd.read_csv(SNAPSHOT_PATH)
    factors_df["order_form_service_id"] = pd.to_numeric(
        factors_df["order_form_service_id"], errors="coerce"
    ).astype("Int64")

    out_rows: list[dict] = []
    rfp_dropped = 0
    attempts = 0
    max_attempts = n_rows * 6

    while len(out_rows) < n_rows and attempts < max_attempts:
        attempts += 1
        service_id = weighted_choice(rng, SERVICE_WEIGHTS)
        inp = sample_input(rng, np_rng, service_id)

        svc_factors = factors_df[factors_df["order_form_service_id"] == service_id].copy()

        travel_difficulty_desc = lookup_description(
            factors_df, service_id, "Travel Difficulty", inp["travel_difficulty_level"]
        )
        site_complexity_desc = lookup_description(
            factors_df, service_id, "Site Complexity", inp["site_complexity_level"]
        )
        prior_report_desc = lookup_description(
            factors_df, service_id, "Prior Report", inp["prior_report_level"]
        )

        try:
            result = calculate(
                svc_factors,
                base_fee=inp["base_fee"],
                tat=inp["tat"],
                portfolio_size=float(inp["portfolio_size"]),
                building_area=inp["building_area"],
                land_area=inp["land_area"],
                facility_type=inp["facility_type"],
                secondary_property_type=inp["secondary_property_type"],
                limit_of_liability=inp["limit_of_liability"],
                travel_difficulty_level=inp["travel_difficulty_level"],
                prior_report=prior_report_desc,
                site_complexity=site_complexity_desc,
                country_code=inp["country_code"],
                number_of_stories=float(inp["number_of_stories"]),
                number_of_buildings=float(inp["number_of_buildings"]),
                total_units=float(inp["total_units"]),
                percent_units_to_inspect=float(inp["percent_units_to_inspect"]),
            )
        except Exception:
            continue

        if result["is_rfp"] or not isinstance(result["total_fee"], (int, float)):
            rfp_dropped += 1
            continue

        rule_total = float(result["total_fee"])
        # Lognormal noise σ≈0.07 ⇒ ~±7% market variance around the rule-based price.
        noisy_total = rule_total * float(np.exp(np_rng.normal(loc=0.0, scale=0.07)))
        noisy_total = max(noisy_total, inp["base_fee"] * 0.5)

        out_rows.append(
            {
                "order_form_service_id": service_id,
                "base_fee": inp["base_fee"],
                "tat": inp["tat"],
                "portfolio_size": inp["portfolio_size"],
                "building_area": inp["building_area"],
                "land_area": inp["land_area"],
                "facility_type": inp["facility_type"],
                "secondary_property_type": inp["secondary_property_type"],
                "limit_of_liability": inp["limit_of_liability"],
                "travel_difficulty": travel_difficulty_desc,
                "prior_report": prior_report_desc,
                "site_complexity": site_complexity_desc,
                "country_code": inp["country_code"],
                "number_of_stories": inp["number_of_stories"],
                "number_of_buildings": inp["number_of_buildings"],
                "total_units": inp["total_units"],
                "percent_units_to_inspect": inp["percent_units_to_inspect"],
                "rule_based_total_fee": rule_total,
                "total_fee": round(noisy_total, 2),
            }
        )

    df = pd.DataFrame(out_rows, columns=ML_INPUT_COLUMNS + ["rule_based_total_fee"])
    print(f"Generated {len(df)} rows. Dropped {rfp_dropped} RFP rows. Attempts: {attempts}")
    print(f"Service distribution:\n{df['order_form_service_id'].value_counts().sort_index()}")
    print(f"Facility distribution (top 8):\n{df['facility_type'].value_counts().head(8)}")
    print(
        "total_fee stats:",
        f"min={df['total_fee'].min():.0f}",
        f"mean={df['total_fee'].mean():.0f}",
        f"median={df['total_fee'].median():.0f}",
        f"max={df['total_fee'].max():.0f}",
        f"std={df['total_fee'].std():.0f}",
    )
    return df


def main() -> None:
    n_rows = int(os.environ.get("N_ROWS", "8000"))
    seed = int(os.environ.get("SEED", "42"))
    df = generate(n_rows=n_rows, seed=seed)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {OUTPUT_PATH} ({len(df)} rows)")


if __name__ == "__main__":
    main()
