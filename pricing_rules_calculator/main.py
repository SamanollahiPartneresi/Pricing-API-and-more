"""
Pricing Rules Calculator (Streamlit Data App).

Mirrors the canonical Ruby `Pricing.main_algo` (SL_Heaven, app/models/pricing.rb)
against the `pricing_factors` table in Keboola Storage. Rule-based; no ML.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd
import streamlit as st

# ### INJECTED_CODE ####
# ### QUERY DATA FUNCTION ####

_RESULTS_PAGE_SIZE = 500


def query_data(query: str) -> pd.DataFrame:
    branch_id = os.environ.get("BRANCH_ID")
    workspace_id = os.environ.get("WORKSPACE_ID")
    token = os.environ.get("KBC_TOKEN")
    kbc_url = os.environ.get("KBC_URL")

    if not branch_id or not workspace_id or not token or not kbc_url:
        raise RuntimeError(
            "Missing required environment variables: BRANCH_ID, WORKSPACE_ID, KBC_TOKEN, KBC_URL."
        )

    query_service_url = kbc_url.replace("connection.", "query.", 1).rstrip("/") + "/api/v1"

    if token.startswith("Bearer "):
        headers = {"Authorization": token, "Accept": "application/json"}
    else:
        headers = {"X-StorageAPI-Token": token, "Accept": "application/json"}

    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=None)
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    with httpx.Client(timeout=timeout, limits=limits) as client:
        response = client.post(
            f"{query_service_url}/branches/{branch_id}/workspaces/{workspace_id}/queries",
            json={"statements": [query]},
            headers=headers,
        )
        response.raise_for_status()
        submission = response.json()
        job_id = submission.get("queryJobId")
        if not job_id:
            raise RuntimeError("Query Service did not return a job identifier.")

        start_ts = time.monotonic()
        while True:
            status_response = client.get(
                f"{query_service_url}/queries/{job_id}", headers=headers
            )
            status_response.raise_for_status()
            job_info = status_response.json()
            status = job_info.get("status")
            if status in {"completed", "failed", "canceled"}:
                break
            if time.monotonic() - start_ts > 300:
                raise TimeoutError(f'Timed out waiting for query "{job_id}" to finish.')
            time.sleep(1)

        statements = job_info.get("statements") or []
        if not statements:
            raise RuntimeError("Query Service returned no statements for the executed query.")
        statement_id = statements[0]["id"]

        columns: list[str] = []
        all_rows: list[list[str]] = []
        offset = 0
        total_rows = None

        while True:
            results_response = client.get(
                f"{query_service_url}/queries/{job_id}/{statement_id}/results",
                headers=headers,
                params={"offset": offset, "pageSize": _RESULTS_PAGE_SIZE},
            )
            results_response.raise_for_status()
            results = results_response.json()

            if results.get("status") != "completed":
                raise ValueError(f'Error when executing query "{query}": {results.get("message")}.')

            if not columns:
                columns = [col["name"] for col in results.get("columns", [])]
                total_rows = results.get("numberOfRows")

            page_rows = results.get("data", [])
            if not page_rows:
                break

            all_rows.extend(page_rows)
            offset += len(page_rows)

            if total_rows is not None and offset >= total_rows:
                break
            if total_rows is None and len(page_rows) < _RESULTS_PAGE_SIZE:
                break

        data_rows = [
            {col_name: value for col_name, value in zip(columns, row)} for row in all_rows
        ]
        return pd.DataFrame(data_rows)


# ### END_OF_INJECTED_CODE ####


# Canonical constants (mirror Constants::OrderFormConstantsHelper)

FACTORS_TABLE = '"SAPI_10556"."in.c-Pricing_Agent_Input_Data"."pricing_factors"'

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

PRIOR_REPORT_VALUES = [
    "External < 2 years",
    "Internal < 10 years",
    "Internal < 2 years",
    "Internal < 6 months",
]
SITE_COMPLEXITY_VALUES = ["Simple", "Average", "Complicated", "Difficult"]
PERCENT_UNITS_TO_INSPECT = list(range(0, 105, 5))

# Size-precedence (corrects Ruby's `order(level: :asc)` alphabetical-sort bug
# when levels are strings like XS/S/M/L/XL — see audit notes).
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


# Factor parsers (mirror Ruby helpers in app/models/pricing.rb)


@dataclass(frozen=True)
class FactorMatch:
    category: str
    level: str
    description: str
    raw_value: str
    percentage: float | str  # float (e.g. 0.10) or 'RFP'


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
    """Ruby: parse_range_bounds. Handles '1', '2 to 3', '3+'. Returns None on blank."""
    s = str(range_string or "").strip()
    if not s:
        return None
    if "to" in s:
        lo_s, hi_s = s.split("to", 1)
        return int(lo_s.strip()), float(int(hi_s.strip()))
    if "+" in s:
        lo = int(s.replace("+", "").strip())
        return lo, float("inf")
    n = int(s)
    return n, float(n)


def parse_liability_range_bounds(range_string: str) -> tuple[int, float] | None:
    """Ruby: parse_liability_range_bounds."""
    s = str(range_string or "").strip().replace(",", "")
    if not s:
        return None
    lower = s.lower()
    import re

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


# Per-category resolvers — each returns a FactorMatch or None (no match → RFP)


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
    """Ruby: Pricing.turnaround_time. Parses 'less than/greater than or equal to X days'."""
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
        # Ruby fallback: if tat exceeds the last level's day value, use the last factor.
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
    """Generic range lookup with parse_range_bounds. Used for Portfolio Size, # of Stories, # of Buildings, Unit Inspection."""
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
    facility_type: str,
    secondary_property_type: str,
) -> FactorMatch | None:
    """Ruby: Pricing.size_percentage. Uses Building SF descriptions ordered by SIZE_LEVEL_PRECEDENCE."""
    if facility_type in UNIT_INSPECTION_FACILITY_TYPE:
        return None  # size fee skipped; units fee handles it

    cat = factors_for(df, "Size").copy()
    if cat.empty:
        return None

    cat["_size_order"] = cat["level"].map(
        lambda lvl: SIZE_LEVEL_PRECEDENCE.get(str(lvl).upper(), 999)
    )
    cat = cat.sort_values("_size_order")

    # Vacant Land special case: prefer the smallest tier (the Ruby looked for
    # 'XS: Applied to certain Primary Property Types' which doesn't exist in
    # the current DB; falling back to the smallest available tier is the
    # closest intent-preserving behaviour).
    if str(secondary_property_type).strip() == "Vacant Land":
        return make_match(cat.iloc[0])

    for _, row in cat.iterrows():
        if matches_size_condition(str(row["description"]), building_area):
            return make_match(row)
    return None


def matches_size_condition(description: str, building_sf: float) -> bool:
    """Parse 'S: Building SF <= 30000' / 'XL: Building SF > 250000' / OR-joined conditions."""
    import re

    condition_text = description.split(":", 1)[-1].strip()
    parts = re.split(r"\s+or\s+", condition_text, flags=re.IGNORECASE)
    for part in parts:
        if matches_one_size_condition(part, building_sf):
            return True
    return False


def matches_one_size_condition(condition: str, building_sf: float) -> bool:
    import re

    c = condition.strip()
    if "building sf" in c.lower():
        rest = re.sub(r"building\s+sf", "", c, flags=re.IGNORECASE).strip()
        m = re.match(r"([<>=]+)\s*([\d,]+)", rest)
        if not m:
            return False
        op = m.group(1).strip()
        threshold = float(m.group(2).replace(",", ""))
        return _compare(building_sf, op, threshold)
    # 'Land Ac' parsing intentionally omitted — current DB has no such rows.
    return False


def _compare(value: float, op: str, threshold: float) -> bool:
    return {
        "<=": value <= threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        ">": value > threshold,
        "=": value == threshold,
    }.get(op, False)


def resolve_travel_difficulty(df: pd.DataFrame, level: int | None) -> FactorMatch | None:
    """Ruby: Pricing.travel_difficulty_percentage. Match by integer level."""
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
    """Case-insensitive description match. Used for Prior Report, Site Complexity, International."""
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
    """Ruby: Pricing.time_period_percentage. Always level '1'."""
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
    """Ruby: Pricing.units_percentage."""
    if facility_type not in UNIT_INSPECTION_FACILITY_TYPE:
        return None
    if total_units <= 0 or percent_to_inspect <= 0:
        return None
    units_to_inspect = int(total_units * (percent_to_inspect / 100.0))
    return resolve_simple_range(df, "Unit Inspection", units_to_inspect)


def resolve_buildings(
    df: pd.DataFrame, facility_type: str, number_of_buildings: float
) -> FactorMatch | None:
    """Ruby: Pricing.number_of_buildings. Uses single '# of Buildings' category in this DB
    (Ruby's # of Buildings 1 / 2 split is not present); still gated by facility-type eligibility."""
    if facility_type not in NUMBER_OF_BUILDINGS_ELIGIBLE:
        return None
    if number_of_buildings <= 0:
        return None
    return resolve_simple_range(df, "# of Buildings", number_of_buildings)


# Main algorithm (mirrors Pricing.main_algo)


@dataclass
class FeeOutcome:
    factor: FactorMatch | None
    amount: int | str  # int dollars, or 'RFP'


def fee_outcome(factor: FactorMatch | None, base_fee: float, *, fallback_rfp_when_missing: bool) -> FeeOutcome:
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
    # Special Purpose → all RFP
    if facility_type.strip().lower() == "special purpose":
        return _all_rfp_result(base_fee)

    # 'fallback_rfp_when_missing': True means "this factor should always apply
    # for these inputs; absence = quote manually". False means "blank input is
    # legitimately 0% (factor doesn't apply)".

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
        "time_period_fee": fee_outcome(resolve_time_period(df), base_fee, fallback_rfp_when_missing=False),
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
            resolve_size(df, building_area, facility_type, secondary_property_type), base_fee,
            fallback_rfp_when_missing=(
                facility_type not in UNIT_INSPECTION_FACILITY_TYPE and building_area > 0
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


# Streamlit UI


st.set_page_config(page_title="Pricing Rules Calculator", page_icon="📋", layout="wide")
st.title("Pricing Rules Calculator")
st.caption(
    "Rule-based pricing engine. Mirrors the canonical `Pricing.main_algo` (SL_Heaven) "
    "against the `pricing_factors` table in Keboola Storage."
)


@st.cache_data(ttl=300)
def load_all_factors() -> pd.DataFrame:
    df = query_data(
        f'SELECT "order_form_service_id", "category", "level", "description", "value" '
        f"FROM {FACTORS_TABLE}"
    )
    for col in ("category", "level", "description"):
        df[col] = df[col].astype(str)
    df["order_form_service_id"] = pd.to_numeric(
        df["order_form_service_id"], errors="coerce"
    ).astype("Int64")
    return df


with st.spinner("Loading pricing factors..."):
    try:
        all_factors = load_all_factors()
    except Exception as exc:
        st.error(f"Could not load pricing factors: {exc}")
        st.stop()


# Service selector (always required)
service_ids = sorted(
    int(x) for x in all_factors["order_form_service_id"].dropna().unique().tolist()
)
if not service_ids:
    st.error("`pricing_factors` table is empty.")
    st.stop()

# Maps `order_form_service_id` to human-readable service names.
# ID 1 = PCA (inferred from factor categories: Building SF tiers,
# # of Stories, # of Buildings, Unit Inspection — all PCA-specific).
# IDs 2 and 3 are demo seed values for ESA and Zoning (see
# demo_data/pricing_factors_esa_zoning.csv). Update if real
# order_form_services records get loaded with different IDs.
SERVICE_NAMES = {1: "PCA", 2: "ESA", 3: "Zoning"}
service_options = ["— Select a service —"] + [
    SERVICE_NAMES.get(sid, f"Service {sid}") for sid in service_ids
]

service_label = st.selectbox("Service", service_options, index=0, key="service_pick")
if service_label.startswith("—"):
    st.info("Pick a service to load its pricing factors.")
    st.stop()

selected_service_id = service_ids[service_options.index(service_label) - 1]
factors_df = all_factors[all_factors["order_form_service_id"] == selected_service_id].copy()

if len(service_ids) == 1:
    st.warning(
        f"Only one service is currently seeded in `pricing_factors` "
        f"(`order_form_service_id = {selected_service_id}`). "
        "Add rows for PCA / ESA / Zoning when ready."
    )

with st.expander(f"View {len(factors_df)} pricing factors for this service"):
    st.dataframe(
        factors_df[["category", "level", "description", "value"]],
        width="stretch",
        hide_index=True,
    )


# Project inputs
st.markdown("### Project inputs")
col1, col2, col3 = st.columns(3)

with col1:
    base_fee_in = st.number_input("Base fee ($)", min_value=0.0, value=5000.0, step=100.0)
    tat_in = st.number_input("Turnaround (days)", min_value=0, value=5, step=1)
    facility_type_in = st.selectbox("Facility type", PRIMARY_PROPERTY_TYPES, index=0)
    secondary_in = st.text_input(
        "Secondary property type",
        value="",
        help="Free text. Use 'Vacant Land' to trigger the Size special case.",
    )
    country_in = st.selectbox(
        "Country / region",
        ["", "US", "CA"],
        index=0,
        help="Matched case-insensitively against the International factor `description`.",
    )

with col2:
    building_area_in = st.number_input(
        "Building area (SF)", min_value=0, value=40000, step=1000
    )
    number_of_stories_in = st.number_input(
        "Number of stories", min_value=0, value=2, step=1
    )
    number_of_buildings_in = st.number_input(
        "Number of buildings", min_value=0, value=1, step=1,
        help="Applied only for facility types in the buildings-eligibility list.",
    )
    portfolio_size_in = st.number_input(
        "Portfolio size (# of properties)", min_value=0, value=1, step=1
    )
    limit_of_liability_in = st.number_input(
        "Limit of liability ($)", min_value=0, value=0, step=50000,
        help="Auto-matched to the right tier in the Limit of Liability factor.",
    )

with col3:
    travel_options = factors_df[factors_df["category"] == "Travel Difficulty"].copy()
    travel_options["_lvl"] = pd.to_numeric(travel_options["level"], errors="coerce")
    travel_options = travel_options.sort_values("_lvl")
    travel_choices = ["— None —"] + [
        f"{row['level']} — {row['description']}"
        for _, row in travel_options.iterrows()
    ]
    travel_pick = st.selectbox("Travel difficulty", travel_choices, index=0)
    travel_level: int | None = (
        None if travel_pick.startswith("—") else int(travel_pick.split(" ")[0])
    )

    prior_in = st.selectbox(
        "Prior report", [""] + PRIOR_REPORT_VALUES, index=0
    )
    site_in = st.selectbox(
        "Site complexity", [""] + SITE_COMPLEXITY_VALUES, index=0
    )
    units_eligible = facility_type_in in UNIT_INSPECTION_FACILITY_TYPE
    total_units_in = st.number_input(
        "Total units",
        min_value=0,
        value=0,
        step=10,
        disabled=not units_eligible,
        help="Applied only for Multi-Family / Seniors Housing.",
    )
    percent_in = st.selectbox(
        "Percent units to inspect (%)",
        PERCENT_UNITS_TO_INSPECT,
        index=0,
        disabled=not units_eligible,
    )


go = st.button("Calculate pricing", type="primary", use_container_width=True)
if not go:
    st.stop()


result = calculate(
    factors_df,
    base_fee=base_fee_in,
    tat=int(tat_in),
    portfolio_size=portfolio_size_in,
    building_area=building_area_in,
    facility_type=facility_type_in,
    secondary_property_type=secondary_in,
    limit_of_liability=limit_of_liability_in,
    travel_difficulty_level=travel_level,
    prior_report=prior_in or None,
    site_complexity=site_in or None,
    country_code=country_in or None,
    number_of_stories=number_of_stories_in,
    number_of_buildings=number_of_buildings_in,
    total_units=total_units_in,
    percent_units_to_inspect=percent_in,
    always_include_tat=False,
)


# Results
st.markdown("---")
st.markdown("### Results")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Base fee", f"${result['base_fee']:,.0f}")
m2.metric("Subtotal", f"${result['subtotal_before_rounding']:,.0f}")
total_display = (
    "RFP" if result["total_fee"] == "RFP" else f"${result['total_fee']:,.0f}"
)
m3.metric("Total fee", total_display)
m4.metric("RFP?", "Yes" if result["is_rfp"] else "No")


st.markdown("### Fee breakdown")
rows = []
for col, label in FEE_COLUMNS:
    outcome: FeeOutcome = result["outcomes"][col]
    amount = outcome.amount
    factor = outcome.factor
    if amount == "RFP":
        amount_display = "RFP"
        pct_display = factor.raw_value + "%" if factor and factor.percentage == "RFP" else "—"
    elif amount == 0 and factor is None:
        amount_display = "—"
        pct_display = "—"
    else:
        amount_display = f"${amount:,}" if isinstance(amount, int) and amount >= 0 else f"-${abs(amount):,}"
        pct_display = f"{float(factor.percentage) * 100:+.1f}%" if factor else "—"
    rows.append(
        {
            "Category": label,
            "Matched rule": factor.description if factor else "— (input not provided / not applicable)",
            "Level": factor.level if factor else "—",
            "Factor": pct_display,
            "Amount": amount_display,
        }
    )
breakdown_df = pd.DataFrame(rows)
st.dataframe(breakdown_df, width="stretch", hide_index=True)


if result["tat_totals"]:
    st.markdown("### Total fee at each turnaround option")
    tat_totals = result["tat_totals"]
    tat_df = pd.DataFrame(
        [
            {"Days": int(d), "Total fee": "RFP" if v == "RFP" else f"${v:,}"}
            for d, v in tat_totals.items()
        ]
    )
    st.dataframe(tat_df, width="stretch", hide_index=True)


with st.expander("Input snapshot"):
    st.json(
        {
            "service": {"id": selected_service_id, "label": service_label},
            "inputs": {
                "base_fee": base_fee_in,
                "tat": int(tat_in),
                "facility_type": facility_type_in,
                "secondary_property_type": secondary_in,
                "country_code": country_in,
                "building_area": building_area_in,
                "number_of_stories": number_of_stories_in,
                "number_of_buildings": number_of_buildings_in,
                "portfolio_size": portfolio_size_in,
                "limit_of_liability": limit_of_liability_in,
                "travel_difficulty_level": travel_level,
                "prior_report": prior_in,
                "site_complexity": site_in,
                "total_units": total_units_in,
                "percent_units_to_inspect": percent_in,
            },
            "result": {
                "total_fee": result["total_fee"],
                "is_rfp": result["is_rfp"],
                "subtotal_before_rounding": result["subtotal_before_rounding"],
                "fees": result["fees"],
            },
        }
    )


st.caption(
    "Powered by Keboola Data Apps · Mirrors `Pricing.main_algo` from SL_Heaven · "
    "ML model not used."
)
