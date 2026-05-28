"""
Rule-based pricing engine — Python port of SL_Heaven's `Pricing.main_algo`.

Source of truth: `app/models/pricing.rb` in the SL_Heaven repo. This module
operates on a `pricing_factors` DataFrame (columns: order_form_service_id,
category, level, description, value) and replicates the canonical 12-category
fee calculation, RFP fallback rules, and per-TAT-day totals.

Used by:
    * `pricing_rules_calculator/main.py` (Streamlit UI)
    * `api.py` (Flask API)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd


# Canonical constants (mirror Constants::OrderFormConstantsHelper in SL_Heaven)

PRIMARY_PROPERTY_TYPES = [
    "Industrial",
    "Healthcare",
    "Lodging",
    "Manufactured Housing",
    "Multi-Family",
    "Office",
    "Retail - Large",
    "Retail - Small",
    "Retail - Specialty",
    "Storage",
    "Seniors Housing",
    "Special Purpose",
    "Other",
]

UNIT_INSPECTION_FACILITY_TYPE = {"Multi-Family", "Seniors Housing"}

NUMBER_OF_BUILDINGS_FACILITY_TYPE_1 = {"Multi-Family", "Seniors Housing", "Storage"}
NUMBER_OF_BUILDINGS_FACILITY_TYPE_2 = {
    "Industrial",
    "Healthcare",
    "Lodging",
    "Manufactured Housing",
    "Other",
    "Office",
    "Retail - Large",
    "Retail - Small",
    "Retail - Specialty",
    "Special Purpose",
}
NUMBER_OF_BUILDINGS_ELIGIBLE = (
    NUMBER_OF_BUILDINGS_FACILITY_TYPE_1 | NUMBER_OF_BUILDINGS_FACILITY_TYPE_2
)

PERCENT_UNITS_TO_INSPECT = list(range(0, 105, 5))

# Corrects Ruby's `order(level: :asc)` alphabetical-sort bug for string levels.
SIZE_LEVEL_PRECEDENCE = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "2XL": 5, "3XL": 6}

# Fee column order matches the canonical Ruby result hash.
FEE_COLUMNS: list[tuple[str, str]] = [
    ("limit_of_liability_fee", "Limit of Liability"),
    ("tat_fee", "Turnaround Time"),
    ("portfolio_fee", "Portfolio Size"),
    ("time_period_fee", "Time Period"),
    ("travel_difficulty_fee", "Travel Difficulty"),
    ("prior_report_fee", "Prior Report"),
    ("site_complexity_fee", "Site Complexity"),
    ("international_fee", "International"),
    ("units_fee", "Unit Inspection"),
    ("size_fee", "Size"),
    ("buildings_fee", "# of Buildings"),
    ("stories_fee", "# of Stories"),
]

# Maps order_form_service_id to human-readable service names.
SERVICE_NAMES = {1: "PCA Equity", 2: "ESA", 3: "Zoning", 4: "PCA Debt"}

# Production base fees from SL_Heaven Master Order Form Services list
# (OrderFormService.base_price for algorithm-enabled services). Zoning is
# not algorithm-enabled in production; placeholder $2,500.
SERVICE_BASE_FEES: dict[int, float] = {
    1: 4000.0,
    2: 2200.0,
    3: 2500.0,
    4: 2400.0,
}


@dataclass(frozen=True)
class FactorMatch:
    category: str
    level: str
    description: str
    raw_value: str
    percentage: float | str


@dataclass
class FeeOutcome:
    factor: FactorMatch | None
    amount: int | str


# Factor parsers


def factor_value_to_percentage(raw: str) -> float | str:
    """Ruby: factor_value_to_percentage. Returns 'RFP' or a 0..1 float."""
    s = str(raw).strip().lower()
    if s == "rfp":
        return "RFP"
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return "RFP"


def parse_range_bounds(range_string: str) -> tuple[int, float] | None:
    """Ruby: parse_range_bounds. Handles '1', '2 to 3', '3+', and trailing
    units like '1 to 8 bldgs' or '20+ bldgs' (Ruby's lenient .to_i parses
    leading digits and discards the rest)."""
    s = str(range_string or "").strip()
    if not s:
        return None

    def _leading_int(token: str) -> int:
        m = re.match(r"-?\d+", token.strip())
        return int(m.group(0)) if m else 0

    if "to" in s:
        lo_s, hi_s = s.split("to", 1)
        return _leading_int(lo_s), float(_leading_int(hi_s))
    if "+" in s:
        return _leading_int(s.split("+", 1)[0]), float("inf")
    n = _leading_int(s)
    return n, float(n)


def parse_liability_range_bounds(range_string: str) -> tuple[int, float] | None:
    """Ruby: parse_liability_range_bounds."""
    s = str(range_string or "").strip().replace(",", "")
    if not s:
        return None
    lower = s.lower()

    m = re.search(r"less than or equal to (\d+)", lower)
    if m:
        return 0, float(int(m.group(1)))
    m = re.search(r"between (\d+) and (\d+)", lower)
    if m:
        return int(m.group(1)), float(int(m.group(2)))
    m = re.search(r"(\d+) or greater", lower) or re.search(
        r"greater than or equal to (\d+)", lower
    )
    if m:
        return int(m.group(1)), float("inf")
    return None


# Per-category resolvers


def factors_for(df: pd.DataFrame, category: str) -> pd.DataFrame:
    return df[df["category"] == category]


def make_match(row: pd.Series) -> FactorMatch:
    raw = row["value"]
    pct = factor_value_to_percentage(raw)
    return FactorMatch(
        category=row["category"],
        level=str(row["level"]),
        description=str(row["description"]),
        raw_value=str(raw),
        percentage=pct,
    )


def resolve_turnaround_time(df: pd.DataFrame, tat: int) -> FactorMatch | None:
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

    return make_match(chosen)


def resolve_simple_range(
    df: pd.DataFrame, category: str, numeric_value: float
) -> FactorMatch | None:
    cat = factors_for(df, category).copy()
    if cat.empty:
        return None
    cat["_lvl"] = pd.to_numeric(cat["level"], errors="coerce")
    cat = cat.sort_values("_lvl")
    for _, row in cat.iterrows():
        bounds = parse_range_bounds(row["description"])
        if bounds is None:
            continue
        lo, hi = bounds
        if lo <= numeric_value <= hi:
            return make_match(row)
    return None


def resolve_limit_of_liability(df: pd.DataFrame, liability: float) -> FactorMatch | None:
    cat = factors_for(df, "Limit of Liability").copy()
    if cat.empty:
        return None
    cat["_lvl"] = pd.to_numeric(cat["level"], errors="coerce")
    cat = cat.sort_values("_lvl")
    for _, row in cat.iterrows():
        bounds = parse_liability_range_bounds(row["description"])
        if bounds is None:
            continue
        lo, hi = bounds
        if lo <= liability <= hi:
            return make_match(row)
    return None


def resolve_size(
    df: pd.DataFrame,
    building_area: float,
    land_area: float,
    facility_type: str,
    secondary_property_type: str,
) -> FactorMatch | None:
    if facility_type in UNIT_INSPECTION_FACILITY_TYPE:
        return None

    cat = factors_for(df, "Size").copy()
    if cat.empty:
        return None

    def _sort_key(lvl: Any) -> tuple[int, float]:
        s = str(lvl)
        try:
            return (0, float(s))
        except ValueError:
            return (1, float(SIZE_LEVEL_PRECEDENCE.get(s.upper(), 999)))

    cat["_size_order"] = cat["level"].map(_sort_key)
    cat = cat.sort_values("_size_order")

    if str(secondary_property_type).strip() == "Vacant Land":
        vacant = cat[
            cat["description"].str.contains(
                "Applied to certain Primary Property Types",
                case=False,
                na=False,
            )
        ]
        if not vacant.empty:
            return make_match(vacant.iloc[0])
        return make_match(cat.iloc[0])

    for _, row in cat.iterrows():
        if matches_size_condition(str(row["description"]), building_area, land_area):
            return make_match(row)
    return None


def matches_size_condition(description: str, building_sf: float, land_ac: float) -> bool:
    condition_text = description.split(":", 1)[-1].strip()
    parts = re.split(r"\s+or\s+", condition_text, flags=re.IGNORECASE)
    for part in parts:
        if matches_one_size_condition(part, building_sf, land_ac):
            return True
    return False


def matches_one_size_condition(condition: str, building_sf: float, land_ac: float) -> bool:
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


def _compare(value: float, op: str, threshold: float) -> bool:
    return {
        "<=": value <= threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        ">": value > threshold,
        "=": value == threshold,
    }.get(op, False)


def resolve_travel_difficulty(df: pd.DataFrame, level: int | None) -> FactorMatch | None:
    if level is None or level == 0:
        return None
    cat = factors_for(df, "Travel Difficulty")
    rows = cat[cat["level"].astype(str) == str(level)]
    if rows.empty:
        return None
    return make_match(rows.iloc[0])


def resolve_by_description(
    df: pd.DataFrame, category: str, description: str | None
) -> FactorMatch | None:
    if not description:
        return None
    cat = factors_for(df, category)
    if cat.empty:
        return None
    matches = cat[cat["description"].str.lower() == str(description).lower()]
    if matches.empty:
        return None
    return make_match(matches.iloc[0])


def resolve_time_period(df: pd.DataFrame) -> FactorMatch | None:
    cat = factors_for(df, "Time Period")
    if cat.empty:
        return None
    rows = cat[cat["level"].astype(str) == "1"]
    if rows.empty:
        return None
    return make_match(rows.iloc[0])


def resolve_units(
    df: pd.DataFrame,
    facility_type: str,
    total_units: float,
    percent_to_inspect: float,
) -> FactorMatch | None:
    if facility_type not in UNIT_INSPECTION_FACILITY_TYPE:
        return None
    if total_units <= 0 or percent_to_inspect <= 0:
        return None
    units_to_inspect = int(total_units * (percent_to_inspect / 100.0))
    return resolve_simple_range(df, "Unit Inspection", units_to_inspect)


def resolve_buildings(
    df: pd.DataFrame, facility_type: str, number_of_buildings: float
) -> FactorMatch | None:
    if number_of_buildings <= 0:
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
    return resolve_simple_range(df, category, number_of_buildings)


# Main algorithm


def fee_outcome(
    factor: FactorMatch | None, base_fee: float, *, fallback_rfp_when_missing: bool
) -> FeeOutcome:
    if factor is None:
        return FeeOutcome(None, "RFP" if fallback_rfp_when_missing else 0)
    if factor.percentage == "RFP":
        return FeeOutcome(factor, "RFP")
    amount = int(round(base_fee * float(factor.percentage)))
    return FeeOutcome(factor, amount)


def calculate(
    df: pd.DataFrame,
    *,
    base_fee: float,
    tat: int,
    portfolio_size: float,
    building_area: float,
    land_area: float,
    facility_type: str,
    secondary_property_type: str,
    limit_of_liability: float,
    travel_difficulty_level: int | None,
    prior_report: str | None,
    site_complexity: str | None,
    country_code: str | None,
    number_of_stories: float,
    number_of_buildings: float,
    total_units: float,
    percent_units_to_inspect: float,
    always_include_tat: bool = False,
) -> dict[str, Any]:
    if facility_type.strip().lower() == "special purpose":
        return _all_rfp_result(base_fee)

    outcomes: dict[str, FeeOutcome] = {
        "limit_of_liability_fee": fee_outcome(
            resolve_limit_of_liability(df, limit_of_liability), base_fee,
            fallback_rfp_when_missing=limit_of_liability > 0,
        ),
        "tat_fee": fee_outcome(
            resolve_turnaround_time(df, tat), base_fee,
            fallback_rfp_when_missing=tat > 0,
        ),
        "portfolio_fee": fee_outcome(
            resolve_simple_range(df, "Portfolio Size", portfolio_size) if portfolio_size > 0 else None,
            base_fee,
            fallback_rfp_when_missing=portfolio_size > 0,
        ),
        "time_period_fee": fee_outcome(
            resolve_time_period(df), base_fee, fallback_rfp_when_missing=False
        ),
        "travel_difficulty_fee": fee_outcome(
            resolve_travel_difficulty(df, travel_difficulty_level), base_fee,
            fallback_rfp_when_missing=(travel_difficulty_level or 0) > 0,
        ),
        "prior_report_fee": fee_outcome(
            resolve_by_description(df, "Prior Report", prior_report), base_fee,
            fallback_rfp_when_missing=bool(prior_report),
        ),
        "site_complexity_fee": fee_outcome(
            resolve_by_description(df, "Site Complexity", site_complexity), base_fee,
            fallback_rfp_when_missing=bool(site_complexity),
        ),
        "international_fee": fee_outcome(
            resolve_by_description(df, "International", country_code), base_fee,
            fallback_rfp_when_missing=bool(country_code),
        ),
        "units_fee": fee_outcome(
            resolve_units(df, facility_type, total_units, percent_units_to_inspect), base_fee,
            fallback_rfp_when_missing=(
                facility_type in UNIT_INSPECTION_FACILITY_TYPE
                and total_units > 0
                and percent_units_to_inspect > 0
            ),
        ),
        "size_fee": fee_outcome(
            resolve_size(df, building_area, land_area, facility_type, secondary_property_type),
            base_fee,
            fallback_rfp_when_missing=(
                facility_type not in UNIT_INSPECTION_FACILITY_TYPE
                and (building_area > 0 or land_area > 0)
            ),
        ),
        "buildings_fee": fee_outcome(
            resolve_buildings(df, facility_type, number_of_buildings), base_fee,
            fallback_rfp_when_missing=(
                facility_type in NUMBER_OF_BUILDINGS_ELIGIBLE and number_of_buildings > 0
            ),
        ),
        "stories_fee": fee_outcome(
            resolve_simple_range(df, "# of Stories", number_of_stories) if number_of_stories > 0 else None,
            base_fee,
            fallback_rfp_when_missing=number_of_stories > 0,
        ),
    }

    fees_only = {k: v.amount for k, v in outcomes.items()}
    is_rfp = any(amt == "RFP" for amt in fees_only.values())
    is_rfp_excluding_tat = any(
        amt == "RFP" for k, amt in fees_only.items() if k != "tat_fee"
    )

    subtotal = base_fee + sum(amt for amt in fees_only.values() if isinstance(amt, int))
    total_fee: int | str = "RFP" if is_rfp else int(math.ceil(subtotal / 50.0) * 50)

    if is_rfp_excluding_tat:
        tat_totals = _rfp_tat_totals(df)
    elif (not is_rfp) or (is_rfp and always_include_tat):
        tat_totals = _calc_tat_totals(
            df, base_fee=base_fee, total_minus_tat=subtotal - (
                fees_only["tat_fee"] if isinstance(fees_only["tat_fee"], int) else 0
            ),
        )
    else:
        tat_totals = None

    return {
        "base_fee": base_fee,
        "fees": fees_only,
        "outcomes": outcomes,
        "subtotal_before_rounding": subtotal,
        "total_fee": total_fee,
        "is_rfp": is_rfp,
        "tat_totals": tat_totals,
    }


def _all_rfp_result(base_fee: float) -> dict[str, Any]:
    fees = {col: "RFP" for col, _ in FEE_COLUMNS}
    outcomes = {col: FeeOutcome(None, "RFP") for col, _ in FEE_COLUMNS}
    return {
        "base_fee": base_fee,
        "fees": fees,
        "outcomes": outcomes,
        "subtotal_before_rounding": base_fee,
        "total_fee": "RFP",
        "is_rfp": True,
        "tat_totals": None,
    }


def _calc_tat_totals(
    df: pd.DataFrame, *, base_fee: float, total_minus_tat: float
) -> dict[str, int | str]:
    cat = factors_for(df, "Turnaround Time")
    if cat.empty:
        return {}
    tat_count = len(cat)
    totals: dict[str, int | str] = {}
    for day in range(1, tat_count + 1):
        match = resolve_turnaround_time(df, day)
        if match is None:
            continue
        if match.percentage == "RFP":
            totals[str(day)] = "RFP"
        else:
            temp_fee = round(base_fee * float(match.percentage))
            totals[str(day)] = int(math.ceil((total_minus_tat + temp_fee) / 50.0) * 50)
    return totals


def _rfp_tat_totals(df: pd.DataFrame) -> dict[str, str]:
    tat_count = len(factors_for(df, "Turnaround Time"))
    return {str(d): "RFP" for d in range(1, tat_count + 1)}


# Helpers for API consumers (Flask + Streamlit can both render breakdowns from this)


def breakdown_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a `calculate()` result into a list of per-category rows suitable
    for tables or JSON responses."""
    rows: list[dict[str, Any]] = []
    for col, label in FEE_COLUMNS:
        outcome: FeeOutcome = result["outcomes"][col]
        factor = outcome.factor
        amount = outcome.amount
        rows.append(
            {
                "category": label,
                "fee_key": col,
                "level": factor.level if factor else None,
                "description": factor.description if factor else None,
                "percentage": (
                    None
                    if factor is None
                    else (
                        "RFP"
                        if factor.percentage == "RFP"
                        else round(float(factor.percentage), 4)
                    )
                ),
                "amount": amount,
            }
        )
    return rows
