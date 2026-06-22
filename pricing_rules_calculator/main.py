"""
Service Pricing Tool (Streamlit Data App).

Mirrors the canonical Ruby `Pricing.main_algo` (SL_Heaven, app/models/pricing.rb)
against the `pricing_factors` table in Keboola Storage, and (optionally) calls
the PricePilot Flask ML API to render both predictions side-by-side.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import pandas as pd
import requests
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

# Historical awarded-fee distribution tables (built by the "Fee stats by service
# and property" Snowflake transformation from in.c-pricing-data-transformation.
# pricing_fee_model_input, scoped to the last 3 years). Used to show users the
# typical fee range (5th pct / median / 95th pct) for the selected service and
# property type.
FEE_STATS_BY_SERVICE_TABLE = '"SAPI_10556"."out.c-pricing_ml"."fee_stats_service_3yr"'
FEE_STATS_BY_SERVICE_PROPERTY_TABLE = (
    '"SAPI_10556"."out.c-pricing_ml"."fee_stats_service_property_3yr"'
)

# Distinct secondary property types (per service, count >= 20, last 3 years),
# built by the same "Fee stats by service and property" transformation. Used to
# populate the Secondary property type dropdown with real historical values.
SECONDARY_PROPERTY_TYPES_TABLE = (
    '"SAPI_10556"."out.c-pricing_ml"."secondary_property_types"'
)

# Slim, last-3-years, fee>0 subset of pricing_fee_model_input (algorithm
# services 1/2/4) with the rule-aligned levers + awarded fee, built by the same
# "Fee stats by service and property" transformation. Queried live (filtered +
# ranked by similarity) to show users real past projects like the one they
# entered, alongside the two estimates.
COMPARABLE_PROJECTS_TABLE = '"SAPI_10556"."out.c-pricing_ml"."comparable_projects"'

# Gain-based feature importance for the deployed PricePilot fee model, regenerated
# on every retrain by the `pricepilot_fee_model_training` transformation. Used to
# render the "What drives the fee" panel next to the ML prediction.
FEATURE_IMPORTANCE_TABLE = '"SAPI_10556"."out.c-pricing_ml"."fee_model_importance"'

# Accuracy metrics + append-only run log for the deployed fee model (regenerated
# on every retrain). Power the "model accuracy" trust panel and version footer.
MODEL_METRICS_TABLE = '"SAPI_10556"."out.c-pricing_ml"."fee_model_metrics"'
MODEL_RUNS_TABLE = '"SAPI_10556"."out.c-pricing_ml"."fee_model_runs"'

# App service id -> the service label used in the model's per-service metric
# scopes (service_test::<name>). Zoning has no historical model coverage yet.
SERVICE_METRIC_SCOPE = {1: "Equity PCA", 2: "Phase I ESA", 4: "Debt PCA"}

# Services the ML fee model was actually trained on. Anything outside this set
# (e.g. Zoning) has no model coverage, so the API would return a meaningless $0
# — we surface a "not available" message for those instead of calling it.
ML_SUPPORTED_SERVICE_IDS = set(SERVICE_METRIC_SCOPE)

# Human-facing app version. Bump on meaningful UI/logic releases.
APP_VERSION = "1.3.0"

# Rule-engine logic version (bump when the factor-matching logic changes).
RULE_ENGINE_VERSION = "1.0.0"

# Show a variance warning when rule-based and ML estimates diverge by more than this.
VARIANCE_WARN_THRESHOLD = 0.25

# Comparable past projects: how many rows to show in the table vs. how large a
# similarity-ranked pool to compute the fee range/median over (the range is more
# meaningful over a wider pool than the handful of rows displayed).
COMPARABLES_DISPLAY_LIMIT = 6
COMPARABLES_STATS_LIMIT = 50

# Friendly display names for the model's raw feature columns.
FEATURE_LABELS = {
    "service_type_id": "Service",
    "turn_around_time": "Turnaround time",
    "customer_type": "Client type",
    "land_acreage": "Land area",
    "building_area": "Building area",
    "secondary_property_type": "Secondary property type",
    "primary_property_type": "Property type",
    "created_month": "Time of year",
    "number_of_buildings": "# of buildings",
    "total_units": "Total units",
    "country": "Country",
    "number_of_stories": "# of stories",
    "site_complexity": "Site complexity",
    "pct_units_inspect": "% units inspected",
    "prior_report": "Prior report",
    "base_fee": "Base fee",
}

# The app's primary-property-type labels differ slightly from the historical
# `primary_property_type` vocabulary; map the few that don't match verbatim.
APP_TO_HIST_PROPERTY = {
    "Storage": "Self Storage",
    "Seniors Housing": "Senior Housing",
}

# ### PRICING_ENGINE ####
# The rule engine is defined once in the repo-root `pricing_engine.py` (the same
# module the Flask API imports). For local dev / lint / tests we import it here;
# the deploy build (`_build_deploy_source.py`) REPLACES this entire block with
# the inlined body of pricing_engine.py, because the Streamlit data app ships as
# a single inline source file with no repo to import from.
import sys as _sys  # noqa: E402
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pricing_engine import (  # noqa: E402,F401
    FEE_COLUMNS,
    FeeOutcome,
    NUMBER_OF_BUILDINGS_ELIGIBLE,
    NUMBER_OF_BUILDINGS_FACILITY_TYPE_1,
    PERCENT_UNITS_TO_INSPECT,
    PRIMARY_PROPERTY_TYPES,
    SERVICE_BASE_FEES,
    SERVICE_NAMES,
    UNIT_INSPECTION_FACILITY_TYPE,
    calculate,
)
# ### END_PRICING_ENGINE ####


# ML API integration (PricePilot Flask app, deployed as a Keboola python-js data app)


ML_API_URL_DEFAULT = os.environ.get(
    "ML_API_URL", "https://pricepilot-api-1304626184.hub.keboola.com"
)


def _ml_param(value: Any) -> str:
    """Convert a Streamlit input to a string the Flask API will accept.
    None/0/'' all become '' so the API sees them as 'not provided'."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and value == 0:
        return ""
    return str(value)


def call_ml_api(
    base_url: str,
    *,
    order_form_service_id: int,
    base_fee: float,
    tat: int,
    portfolio_size: float,
    building_area: float,
    land_area: float,
    facility_type: str,
    secondary_property_type: str,
    customer_type: str,
    limit_of_liability: float,
    travel_difficulty_level: int | None,
    prior_report: str | None,
    site_complexity: str | None,
    country_code: str | None,
    number_of_stories: float,
    number_of_buildings: float,
    total_units: float,
    percent_units_to_inspect: float,
    is_rfp: bool,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Hit the PricePilot Flask API's GET /?api=true endpoint with the same inputs
    the rule-based engine just used. Returns the parsed JSON payload, or raises."""
    params = {
        "api": "true",
        "order_form_service_id": str(order_form_service_id),
        "base_fee": _ml_param(base_fee),
        "tat": _ml_param(tat),
        "portfolio_size": _ml_param(portfolio_size),
        "building_area": _ml_param(building_area),
        "land_area": _ml_param(land_area),
        "facility_type": _ml_param(facility_type),
        "secondary_property_type": _ml_param(secondary_property_type),
        "customer_type": _ml_param(customer_type),
        "limit_of_liability": _ml_param(limit_of_liability),
        "travel_difficulty": _ml_param(travel_difficulty_level),
        "prior_report": _ml_param(prior_report),
        "site_complexity": _ml_param(site_complexity),
        "country_code": _ml_param(country_code),
        "number_of_stories": _ml_param(number_of_stories),
        "number_of_buildings": _ml_param(number_of_buildings),
        "total_units": _ml_param(total_units),
        "percent_units_to_inspect": _ml_param(percent_units_to_inspect),
        "is_rfp": "true" if is_rfp else "false",
    }
    response = requests.get(base_url.rstrip("/") + "/", params=params, timeout=timeout_s)
    response.raise_for_status()
    return response.json()


# Client / client-type lookups now live in the Flask API (see api.py:/clients and
# /client-types) so the same searchable, sorted, cross-filtered logic is reused by
# every frontend. This UI is a thin client: it calls those endpoints and falls
# back to a direct warehouse query only if the API is unreachable.

@st.cache_data(ttl=600, show_spinner=False)
def fetch_clients_api(base_url: str, service_id: int, client_type: str = "") -> list[str]:
    """Client names for a service via GET /clients. `client_type` restricts to
    clients who have booked under that type (the 'vice versa' direction)."""
    params: dict[str, Any] = {"service_id": int(service_id), "limit": 5000}
    if client_type:
        params["client_type"] = client_type
    resp = requests.get(base_url.rstrip("/") + "/clients", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str(data["error"]))
    return [c["name"] for c in data.get("clients", []) if c.get("name")]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_client_types_api(
    base_url: str, service_id: int, client_name: str = ""
) -> tuple[list[str], bool]:
    """Client types for a service via GET /client-types. When `client_name` is
    set, returns that client's own type(s) (most common first) and whether the
    type is unique to the client. Returns (types, is_unique_for_client)."""
    params: dict[str, Any] = {"service_id": int(service_id), "limit": 5000}
    if client_name:
        params["client_name"] = client_name
    resp = requests.get(base_url.rstrip("/") + "/client-types", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str(data["error"]))
    types = [c["name"] for c in data.get("client_types", []) if c.get("name")]
    return types, bool(data.get("is_unique_for_client"))


def get_client_options(base_url: str, service_id: int, client_type: str = "") -> list[str]:
    """API-first client names with a direct-query fallback."""
    try:
        return fetch_clients_api(base_url, service_id, client_type=client_type)
    except Exception:
        try:
            names = load_client_name_options(service_id)
            if client_type:
                cmap = load_client_type_map(service_id)
                names = [n for n in names if client_type in cmap.get(n, [])]
            return names
        except Exception:
            return []


def get_client_type_options(
    base_url: str, service_id: int, client_name: str = ""
) -> tuple[list[str], bool]:
    """API-first client types with a direct-query fallback."""
    try:
        return fetch_client_types_api(base_url, service_id, client_name=client_name)
    except Exception:
        try:
            if client_name:
                types = load_client_type_map(service_id).get(client_name, [])
                return types, len(types) == 1
            return sorted(load_customer_type_options(service_id)), False
        except Exception:
            return [], False


def app_build_label() -> str:
    """Human-readable build identifier shown in the UI.

    Prefer APP_BUILD_VERSION from deployment; otherwise use this file's
    modified timestamp (US Pacific) so users can confirm a redeploy took effect.
    """
    explicit = os.environ.get("APP_BUILD_VERSION", "").strip()
    if explicit:
        return explicit
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        mtime = os.path.getmtime(__file__)
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(
            ZoneInfo("America/Los_Angeles")
        )
        return dt.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    except Exception:
        try:
            mtime = os.path.getmtime(__file__)
            return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(mtime))
        except OSError:
            return "unknown"


# Streamlit UI


st.set_page_config(page_title="Service Pricing Tool", page_icon="📋", layout="wide")
st.title("Service Pricing Tool")
st.caption(f"Version `{APP_VERSION}` · build `{app_build_label()}`")
st.caption(
    "Estimate a project fee two ways at once. The **rule-based engine** applies the "
    "official pricing factors step by step, and the **ML model** predicts a fee from "
    "thousands of past projects. Pick a service and property type, fill in the project "
    "details, then compare both results side by side."
)


# Sidebar: ML API config
with st.sidebar:
    st.markdown("### ML model (PricePilot API)")
    ml_enabled = st.checkbox(
        "Also call the ML model",
        value=True,
        help="When on, the app sends the same inputs to the Flask ML API and shows both "
        "predictions side-by-side.",
    )
    # Endpoint is fixed (the production PricePilot API). Override only via the
    # ML_API_URL environment variable on the data app, not from the UI.
    ml_api_url = ML_API_URL_DEFAULT


@st.cache_data(ttl=300, show_spinner="⏳ Loading pricing factors…")
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


@st.cache_data(ttl=600, show_spinner="⏳ Loading fee benchmarks…")
def load_fee_stats() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the precomputed historical awarded-fee percentiles (p5/median/p95,
    last 3 years) per service and per service x primary property type."""
    by_service = query_data(
        'SELECT "order_form_service_id", "n", "p5_fee", "median_fee", "p95_fee" '
        f"FROM {FEE_STATS_BY_SERVICE_TABLE}"
    )
    by_property = query_data(
        'SELECT "order_form_service_id", "primary_property_type", "n", '
        '"p5_fee", "median_fee", "p95_fee" '
        f"FROM {FEE_STATS_BY_SERVICE_PROPERTY_TABLE}"
    )
    for frame in (by_service, by_property):
        for col in ("order_form_service_id", "n", "p5_fee", "median_fee", "p95_fee"):
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return by_service, by_property


@st.cache_data(ttl=600, show_spinner="⏳ Loading secondary property types…")
def load_secondary_property_types() -> pd.DataFrame:
    """Load distinct secondary property types (per service x primary property
    type, count >= 10, last 3 years) used to populate the Secondary property
    type dropdown. primary/secondary are a hierarchy, so the dropdown is
    filtered by the selected primary property type."""
    df = query_data(
        'SELECT "order_form_service_id", "primary_property_type", '
        '"secondary_property_type", "n" '
        f"FROM {SECONDARY_PROPERTY_TYPES_TABLE}"
    )
    df["order_form_service_id"] = pd.to_numeric(
        df["order_form_service_id"], errors="coerce"
    ).astype("Int64")
    df["n"] = pd.to_numeric(df["n"], errors="coerce")
    df["primary_property_type"] = df["primary_property_type"].astype(str).str.strip()
    df["secondary_property_type"] = df["secondary_property_type"].astype(str).str.strip()
    return df


def secondary_type_options(
    df: pd.DataFrame | None, service_id: int, primary_type: str | None = None
) -> list[str]:
    """Distinct secondary property types for a service, narrowed to the selected
    primary property type (primary -> secondary is a hierarchy), most common
    first. Falls back to the service-wide list if there's no per-primary history,
    and always offers 'Vacant Land' (the rule engine has a Size special case)."""
    opts: list[str] = []
    if df is not None and not df.empty:
        scoped = df[df["order_form_service_id"] == service_id]
        if primary_type:
            hist_primary = APP_TO_HIST_PROPERTY.get(primary_type, primary_type)
            by_primary = scoped[scoped["primary_property_type"] == hist_primary]
            # Prefer the per-primary list; only fall back to service-wide if this
            # primary has no recorded secondary property types.
            source = by_primary if not by_primary.empty else scoped
        else:
            source = scoped
        if source.empty:
            source = df
        ordered = (
            source.groupby("secondary_property_type", as_index=False)["n"]
            .sum()
            .sort_values("n", ascending=False)
        )
        opts = [t for t in ordered["secondary_property_type"].tolist() if t]
    if "Vacant Land" not in opts:
        opts.append("Vacant Land")
    return opts


@st.cache_data(ttl=900, show_spinner="⏳ Loading client types…")
def load_customer_type_options(service_id: int) -> list[str]:
    """Client-type buckets for ONE service (last 3 years), most common first.
    Scoped to the selected service and loaded lazily so the whole
    comparable-projects table isn't scanned on initial page load."""
    df = query_data(
        'SELECT "customer_type", COUNT(*) AS "n" '
        f"FROM {COMPARABLE_PROJECTS_TABLE} "
        "WHERE \"customer_type\" IS NOT NULL AND TRIM(\"customer_type\") <> '' "
        f'AND "order_form_service_id" = {int(service_id)} '
        'GROUP BY 1 ORDER BY "n" DESC'
    )
    if df.empty:
        return []
    return [t.strip() for t in df["customer_type"].astype(str).tolist() if t and t.strip()]


@st.cache_data(ttl=900, show_spinner="⏳ Loading client names…")
def load_client_name_options(service_id: int) -> list[str]:
    """Distinct client names for ONE service (last 3 years), most common first.
    Scoped + lazy: the previous version grouped the entire comparable-projects
    table on every page load, which was the main startup delay."""
    client_expr = (
        'COALESCE(NULLIF(TRIM("client_name"), \'\'), NULLIF(TRIM("company_name"), \'\'))'
    )
    df = query_data(
        f'SELECT {client_expr} AS "client_name", COUNT(*) AS "n" '
        f"FROM {COMPARABLE_PROJECTS_TABLE} "
        f"WHERE {client_expr} IS NOT NULL "
        f'AND "order_form_service_id" = {int(service_id)} '
        'GROUP BY 1 ORDER BY "n" DESC'
    )
    if df.empty:
        return []
    return [t.strip() for t in df["client_name"].astype(str).tolist() if t and t.strip()]


@st.cache_data(ttl=900, show_spinner="⏳ Loading client → type map…")
def load_client_type_map(service_id: int) -> dict[str, list[str]]:
    """Map each client name -> the client type(s) on record for ONE service
    (last 3 years), each client's types ordered most-common first.

    Client type is NOT unique per client: most clients map to a single type,
    but a meaningful minority span several (e.g. a firm that books as both
    'Lender - CMBS' and 'Developer'). We therefore return every type a client
    has used so the UI can offer all of them rather than guessing one."""
    client_expr = (
        'COALESCE(NULLIF(TRIM("client_name"), \'\'), NULLIF(TRIM("company_name"), \'\'))'
    )
    df = query_data(
        f'SELECT {client_expr} AS "client_name", TRIM("customer_type") AS "customer_type", '
        'COUNT(*) AS "n" '
        f"FROM {COMPARABLE_PROJECTS_TABLE} "
        f"WHERE {client_expr} IS NOT NULL "
        "AND \"customer_type\" IS NOT NULL AND TRIM(\"customer_type\") <> '' "
        f'AND "order_form_service_id" = {int(service_id)} '
        'GROUP BY 1, 2 ORDER BY 1, "n" DESC'
    )
    mapping: dict[str, list[str]] = {}
    if df.empty:
        return mapping
    for _, row in df.iterrows():
        client = str(row["client_name"]).strip()
        ctype = str(row["customer_type"]).strip()
        if not client or not ctype:
            continue
        types = mapping.setdefault(client, [])
        if ctype not in types:
            types.append(ctype)
    return mapping


@st.cache_data(ttl=3600, show_spinner="⏳ Loading model feature importance…")
def load_feature_importance() -> pd.DataFrame:
    """Gain-based feature importance for the deployed fee model, ranked high→low,
    with each feature's share of total gain and a friendly display label."""
    df = query_data(
        'SELECT "feature", "gain" '
        f"FROM {FEATURE_IMPORTANCE_TABLE}"
    )
    df["gain"] = pd.to_numeric(df["gain"], errors="coerce").fillna(0.0)
    total = float(df["gain"].sum()) or 1.0
    df["share_pct"] = df["gain"] / total * 100.0
    df["feature_label"] = df["feature"].map(
        lambda f: FEATURE_LABELS.get(str(f), str(f).replace("_", " ").title())
    )
    return df.sort_values("gain", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner="⏳ Loading model accuracy…")
def load_model_metrics() -> pd.DataFrame:
    """Hold-out accuracy metrics for the deployed model, keyed by scope/metric
    (e.g. scope='model_test', metric='within_10pct')."""
    df = query_data(
        'SELECT "scope", "metric", "value" '
        f"FROM {MODEL_METRICS_TABLE}"
    )
    df["scope"] = df["scope"].astype(str)
    df["metric"] = df["metric"].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def metric_value(df: pd.DataFrame | None, scope: str, metric: str) -> float | None:
    if df is None or df.empty:
        return None
    hit = df[(df["scope"] == scope) & (df["metric"] == metric)]
    if hit.empty or pd.isna(hit.iloc[0]["value"]):
        return None
    return float(hit.iloc[0]["value"])


@st.cache_data(ttl=3600, show_spinner="⏳ Loading model version…")
def load_model_run() -> dict[str, Any]:
    """Most recent training run (tag, date, size) for the version footer."""
    df = query_data(
        'SELECT "run_at", "model_tag", "n_features", "rows_total", '
        '"test_mae", "test_within_10pct", "test_r2" '
        f"FROM {MODEL_RUNS_TABLE} "
        'ORDER BY "run_at" DESC LIMIT 1'
    )
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


@st.cache_data(ttl=3600, show_spinner=False)
def load_data_freshness() -> str:
    """Latest month present in the comparable-projects history."""
    df = query_data(
        'SELECT MAX("created_month_label") AS "m" '
        f"FROM {COMPARABLE_PROJECTS_TABLE}"
    )
    if df.empty:
        return ""
    val = df.iloc[0].get("m")
    return "" if val is None else str(val).strip()


def _sql_quote(value: str) -> str:
    """Escape single quotes for safe inlining in a SQL string literal."""
    return str(value).replace("'", "''")


@st.cache_data(ttl=600, show_spinner=False)
def load_comparables(
    service_id: int,
    primary_type: str,
    *,
    secondary_type: str = "",
    client_name: str = "",
    exclude_client: str = "",
    building_area: float = 0.0,
    land_area: float = 0.0,
    uses_building_sf: bool = True,
    min_margin: float | None = None,
    match_service: bool = True,
    match_primary_type: bool = True,
    limit: int = 6,
) -> pd.DataFrame:
    """Return the past projects most similar to the user's inputs: same service
    and primary property type, ranked by closeness on the size dimension the
    service prices on (building SF for PCA, land acres for ESA), preferring an
    exact secondary-type match. Falls back to most-recent when no size is given.
    Each row carries its real awarded fee so users can sanity-check the estimates
    against comparable history. When min_margin is set (0-1 fraction), only past
    projects whose service gross margin exceeds it are returned. When
    match_primary_type is False the property-type filter is dropped, and when
    match_service is False the service filter is dropped too — together these
    power the "other projects for this client" fallback across all services and
    property types (the Service / Property type columns then show what each is)."""
    hist_primary = APP_TO_HIST_PROPERTY.get(primary_type, primary_type)
    where: list[str] = []
    if match_service:
        where.append(f'"order_form_service_id" = {int(service_id)}')
    if match_primary_type:
        where.append(f"\"primary_property_type\" = '{_sql_quote(hist_primary)}'")
    if min_margin is not None:
        where.append(f'TRY_TO_DOUBLE("service_margin") > {float(min_margin)}')
    if client_name:
        where.append(
            'COALESCE(NULLIF(TRIM("client_name"), \'\'), NULLIF(TRIM("company_name"), \'\')) = '
            f"'{_sql_quote(client_name)}'"
        )
    if exclude_client:
        where.append(
            'COALESCE(NULLIF(TRIM("client_name"), \'\'), NULLIF(TRIM("company_name"), \'\')) <> '
            f"'{_sql_quote(exclude_client)}'"
        )
    size_col = "building_area" if uses_building_sf else "land_acreage"
    target = building_area if uses_building_sf else land_area

    order_clauses: list[str] = []
    if secondary_type:
        order_clauses.append(
            "CASE WHEN \"secondary_property_type\" = "
            f"'{_sql_quote(secondary_type)}' THEN 0 ELSE 1 END"
        )
    if target and target > 0:
        # Storage columns come back as strings; cast before the distance math and
        # push rows with no recorded size to the bottom.
        order_clauses.append(
            f'ABS(TRY_TO_DOUBLE("{size_col}") - {float(target)}) ASC NULLS LAST'
        )
    else:
        order_clauses.append('"created_month_label" DESC')
    order_by = ", ".join(order_clauses)

    where_sql = f"WHERE {' AND '.join(where)} " if where else ""
    sql = (
        'SELECT "order_form_service_id", "primary_property_type", "secondary_property_type", '
        '"building_area", "land_acreage", '
        '"year_built", "turn_around_time", "number_of_buildings", "number_of_stories", '
        '"prior_report", "site_complexity", "limit_of_liability_tier", '
        '"city", "state", "country", "customer_type", "client_type", '
        '"client_name", "company_name", "fee", "service_margin", "created_month_label" '
        f"FROM {COMPARABLE_PROJECTS_TABLE} "
        f"{where_sql}"
        f"ORDER BY {order_by} "
        f"LIMIT {int(limit)}"
    )
    df = query_data(sql)
    for col in ("order_form_service_id", "building_area", "land_acreage", "year_built", "number_of_buildings", "number_of_stories", "fee", "service_margin"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fee_hint_caption(
    stats_df: pd.DataFrame | None,
    *,
    service_id: int,
    label: str,
    property_type: str | None = None,
) -> None:
    """Render a compact one-line awarded-fee hint under a selector (5th–95th pct
    with median, last 3 years). No-op when stats are unavailable or there's no
    matching history."""
    if stats_df is None:
        return
    mask = stats_df["order_form_service_id"] == service_id
    if property_type is not None:
        hist_prop = APP_TO_HIST_PROPERTY.get(property_type, property_type)
        mask = mask & (stats_df["primary_property_type"] == hist_prop)
    rows = stats_df[mask]
    if rows.empty:
        return
    r = rows.iloc[0]
    n = int(r["n"]) if pd.notna(r["n"]) else 0
    p5, med, p95 = r["p5_fee"], r["median_fee"], r["p95_fee"]
    if pd.isna(p5) or pd.isna(p95):
        return
    warn = " · ⚠ small sample" if 0 < n < 30 else ""
    st.caption(
        f"💡 {label}: **\\${p5:,.0f}–\\${p95:,.0f}** "
        f"(median \\${med:,.0f}) · {n:,} projects, last 3 yrs{warn}"
    )


def fee_stats_row(
    *, service_id: int, property_type: str | None = None
) -> dict[str, float] | None:
    """Return {n, p5, median, p95} for a service (optionally narrowed to a
    primary property type), falling back to the service-level row when there's
    no property-specific stats. Powers the comparable-count, percentile and
    override captions."""
    if property_type is not None and fee_stats_by_property is not None:
        hist_prop = APP_TO_HIST_PROPERTY.get(property_type, property_type)
        rows = fee_stats_by_property[
            (fee_stats_by_property["order_form_service_id"] == service_id)
            & (fee_stats_by_property["primary_property_type"] == hist_prop)
        ]
        if not rows.empty:
            r = rows.iloc[0]
            return {
                "n": float(r["n"]) if pd.notna(r["n"]) else 0.0,
                "p5": float(r["p5_fee"]) if pd.notna(r["p5_fee"]) else float("nan"),
                "median": float(r["median_fee"]) if pd.notna(r["median_fee"]) else float("nan"),
                "p95": float(r["p95_fee"]) if pd.notna(r["p95_fee"]) else float("nan"),
            }
    if fee_stats_by_service is not None:
        rows = fee_stats_by_service[
            fee_stats_by_service["order_form_service_id"] == service_id
        ]
        if not rows.empty:
            r = rows.iloc[0]
            return {
                "n": float(r["n"]) if pd.notna(r["n"]) else 0.0,
                "p5": float(r["p5_fee"]) if pd.notna(r["p5_fee"]) else float("nan"),
                "median": float(r["median_fee"]) if pd.notna(r["median_fee"]) else float("nan"),
                "p95": float(r["p95_fee"]) if pd.notna(r["p95_fee"]) else float("nan"),
            }
    return None


def estimate_percentile(value: float, stats: dict[str, float] | None) -> float | None:
    """Approximate where `value` sits in the historical fee distribution, using
    the p5/median/p95 anchors (piecewise-linear). Returns 1-99 or None."""
    if not stats:
        return None
    p5, med, p95 = stats.get("p5"), stats.get("median"), stats.get("p95")
    if any(v is None or pd.isna(v) for v in (p5, med, p95)):
        return None
    if value <= p5:
        return 5.0 if p5 <= 0 else max(1.0, 5.0 * value / p5)
    if value >= p95:
        return 99.0
    if value <= med:
        span = (med - p5) or 1.0
        return 5.0 + (value - p5) / span * 45.0
    span = (p95 - med) or 1.0
    return min(99.0, 50.0 + (value - med) / span * 45.0)


def na_field(label: str, reason: str) -> None:
    """Render a greyed-out, disabled placeholder for an input that does not affect
    the result for the current service / property type. Keeps the form grid aligned
    and states the reason inline instead of a vague floating caption."""
    st.text_input(label, value=f"n/a · {reason}", disabled=True)


with st.spinner("Loading pricing factors..."):
    try:
        all_factors = load_all_factors()
    except Exception as exc:
        st.error(f"Could not load pricing factors: {exc}")
        st.stop()


# Service selector (always required)
available_service_ids = {
    int(x) for x in all_factors["order_form_service_id"].dropna().unique().tolist()
}
if not available_service_ids:
    st.error("`pricing_factors` table is empty.")
    st.stop()

# SERVICE_NAMES and SERVICE_BASE_FEES are imported from pricing_engine
# (single source of truth, shared with the Flask API).

# Display order for the dropdown (PCA Debt → PCA Equity → ESA → Zoning).
# Any service id not in this list is appended at the end in id order.
SERVICE_DISPLAY_ORDER = [4, 1, 2, 3]
service_ids = [sid for sid in SERVICE_DISPLAY_ORDER if sid in available_service_ids] + [
    sid for sid in sorted(available_service_ids) if sid not in SERVICE_DISPLAY_ORDER
]
service_options = ["— Select a service —"] + [
    SERVICE_NAMES.get(sid, f"Service {sid}") for sid in service_ids
]

# Historical awarded-fee benchmarks (best-effort; never blocks the calculator).
try:
    fee_stats_by_service, fee_stats_by_property = load_fee_stats()
except Exception:
    fee_stats_by_service, fee_stats_by_property = None, None

# Distinct secondary property types for the dropdown (best-effort).
try:
    secondary_types_df = load_secondary_property_types()
except Exception:
    secondary_types_df = None

# Client-type and client-name options are loaded lazily *after* a service is
# picked (see the Project inputs section) — scoping them to one service avoids a
# full scan of the comparable-projects table on initial page load.


# Prominent provenance line — model + data freshness matter more to users than a
# build timestamp, so surface them up front (details remain in the About footer).
_prov_bits = [f"Rule engine v{RULE_ENGINE_VERSION}"]
try:
    _run = load_model_run()
except Exception:
    _run = {}
if _run:
    _ml_date = str(_run.get("run_at") or "")[:10]
    if _ml_date:
        _prov_bits.append(f"ML model v{_ml_date.replace('-', '.')}")
    try:
        _rows = int(float(_run.get("rows_total")))
        _prov_bits.append(f"{_rows:,} training projects")
    except (TypeError, ValueError):
        pass
try:
    _fresh = load_data_freshness()
except Exception:
    _fresh = ""
if _fresh:
    _prov_bits.append(f"data through {_fresh}")
st.caption("📅 " + " · ".join(_prov_bits))


# Stage 1: Service + Primary Property Type (these drive which inputs are shown below)
st.markdown("### 1. Service & property type")
stage1_col1, stage1_col2 = st.columns(2)
with stage1_col1:
    service_label = st.selectbox("Service", service_options, index=0, key="service_pick")
    if not service_label.startswith("—"):
        _sid = service_ids[service_options.index(service_label) - 1]
        fee_hint_caption(
            fee_stats_by_service, service_id=_sid, label=f"Typical {service_label} fee"
        )
with stage1_col2:
    facility_options = ["— Select primary property type —"] + PRIMARY_PROPERTY_TYPES
    facility_pick = st.selectbox("Primary property type", facility_options, index=0, key="facility_pick")
    if not service_label.startswith("—") and not facility_pick.startswith("—"):
        _sid = service_ids[service_options.index(service_label) - 1]
        fee_hint_caption(
            fee_stats_by_property,
            service_id=_sid,
            label=f"Typical {facility_pick} fee",
            property_type=facility_pick,
        )

if service_label.startswith("—"):
    st.info("Pick a service to load its pricing factors.")
    st.stop()

selected_service_id = service_ids[service_options.index(service_label) - 1]
factors_df = all_factors[all_factors["order_form_service_id"] == selected_service_id].copy()

if facility_pick.startswith("—"):
    st.info(
        "Pick a facility type — inputs below adapt to the property type "
        "(unit-inspection fields appear for Multi-Family / Seniors Housing; "
        "Special Purpose triggers an automatic RFP)."
    )
    st.stop()
facility_type_in = facility_pick

if len(service_ids) == 1:
    st.warning(
        f"Only one service is currently seeded in `pricing_factors` "
        f"(`order_form_service_id = {selected_service_id}`)."
    )

if selected_service_id == 3:
    st.caption(
        "Heads-up: Zoning factors are illustrative placeholder values until "
        "real Zoning coefficients are added to `pricing_factors`."
    )

with st.expander(f"View {len(factors_df)} pricing factors for this service"):
    st.caption(
        "Grouped by pricing lever. Each option shows the adjustment the rule "
        "engine applies for that choice; **RFP** means that option forces a "
        "manual quote."
    )

    _view = factors_df[["category", "level", "description", "value"]].copy()
    _view["category"] = _view["category"].astype(str)
    _view["__rfp"] = _view["value"].astype(str).str.strip().str.upper().eq("RFP")
    _view["__lvl"] = pd.to_numeric(_view["level"], errors="coerce")
    # Stable order: categories as they first appear in the table.
    _categories = list(dict.fromkeys(_view["category"].tolist()))

    _picked = st.multiselect(
        "Filter levers",
        _categories,
        default=_categories,
        key="factor_filter",
        help="Show only the pricing levers you want to inspect.",
    )
    _shown = [c for c in _categories if c in _picked] or _categories

    _factor_cfg = {
        "level": st.column_config.TextColumn("Level", width="small"),
        "description": st.column_config.TextColumn("Option", width="large"),
        "value": st.column_config.TextColumn("Adjustment", width="small"),
    }
    _cols = st.columns(2)
    for _i, _cat in enumerate(_shown):
        _block = _view[_view["category"] == _cat].sort_values(
            ["__lvl", "level"], na_position="last"
        )
        _n_rfp = int(_block["__rfp"].sum())
        _sub = f"**{_cat}** — {len(_block)} option(s)"
        if _n_rfp:
            _sub += f", {_n_rfp} → RFP"
        with _cols[_i % 2]:
            st.markdown(_sub)
            st.dataframe(
                _block[["level", "description", "value"]],
                width="stretch",
                hide_index=True,
                column_config=_factor_cfg,
            )


# Detect which size dimension this service uses from its Size descriptions.
_size_descriptions = factors_df.loc[
    factors_df["category"] == "Size", "description"
].astype(str).str.lower().tolist()
service_uses_building_sf = any("building sf" in d for d in _size_descriptions)
service_uses_land_ac = any("land ac" in d for d in _size_descriptions)

units_eligible = facility_type_in in UNIT_INSPECTION_FACILITY_TYPE
buildings_eligible = facility_type_in in NUMBER_OF_BUILDINGS_ELIGIBLE
size_eligible = (
    not units_eligible
    and facility_type_in.strip().lower() != "special purpose"
)
is_special_purpose = facility_type_in.strip().lower() == "special purpose"


if is_special_purpose:
    st.warning(
        "Special Purpose facilities trigger an automatic RFP — only base fee, "
        "liability, portfolio, and TAT inputs affect the result."
    )


def _sorted_descriptions(category: str) -> list[str]:
    cat = factors_df[factors_df["category"] == category].copy()
    if cat.empty:
        return []
    cat["_lvl"] = pd.to_numeric(cat["level"], errors="coerce")
    cat = cat.sort_values("_lvl")
    return cat["description"].astype(str).tolist()


# Stage 2: Project inputs (conditional on facility type)
st.markdown("### 2. Project inputs")
st.caption(
    "Fields are grouped into **Fee & client**, **Property & size**, and "
    "**Location & risk**. Greyed-out fields don't apply to the selected service "
    "or property type."
)
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("**Fee & client**")
    default_base_fee = SERVICE_BASE_FEES.get(selected_service_id, 5000.0)
    base_fee_in = st.number_input(
        "Base fee ($)",
        min_value=0.0,
        value=default_base_fee,
        step=100.0,
        key=f"base_fee_{selected_service_id}",
        help=(
            f"Starting fee before any factors are applied. Defaults to the standard "
            f"${default_base_fee:,.0f} base for {service_label} — override for a specific quote."
        ),
    )
    tat_in = st.number_input(
        "Turnaround (days)",
        min_value=0,
        value=10,
        step=1,
        help=(
            "Requested delivery time. Faster turnarounds add a rush surcharge; a value "
            "outside the configured rush tiers flags the job as RFP."
        ),
    )
    # Client name + Client type are two-way linked (data served by the Flask API,
    # see fetch_clients_api / fetch_client_types_api):
    #   • pick a client  -> Client type narrows to that client's own type(s)
    #                        (one is auto-selected; several are all listed,
    #                         since client type is NOT unique per client)
    #   • pick a type     -> Client name narrows to clients of that type
    # We read the type widget's current value to filter the client list this same
    # run, and drop any selection the counterpart filter excludes so the widgets
    # stay consistent without oscillating.
    _ANY = "— Any / not specified —"
    _ctype_key = f"client_type_{selected_service_id}"
    _cname_key = f"client_name_{selected_service_id}"
    _prev_cname_key = f"_prev_client_name_{selected_service_id}"

    # Last-touched wins. If the user just picked a NEW client name, that pick
    # takes precedence over the previously-selected client's (now stale) type:
    # otherwise the stale type filter would exclude the new client and the
    # selection would snap back to "Any". When the client name is unchanged we
    # keep filtering the client list by the active type, so picking a *type*
    # still narrows the client list (the other direction of the two-way link).
    _current_cname = st.session_state.get(_cname_key)
    _client_just_changed = (
        _current_cname is not None
        and not str(_current_cname).startswith("—")
        and _current_cname != st.session_state.get(_prev_cname_key)
    )

    _active_type_choice = st.session_state.get(_ctype_key, "")
    _active_type = (
        "" if (not _active_type_choice or _active_type_choice.startswith("—"))
        else _active_type_choice
    )
    if _client_just_changed:
        # New client wins: don't filter the client list by the old client's type
        # and clear that type so it re-derives from this client just below.
        _active_type = ""
        st.session_state.pop(_ctype_key, None)

    # --- Client name (filtered by the active client type) ---
    _client_name_opts = get_client_options(
        ml_api_url, selected_service_id, client_type=_active_type
    )
    _client_options = [_ANY] + _client_name_opts
    # Preserve the user's explicit client even if the type-filtered list (a
    # separate, possibly-inconsistent query) omits it. The Client type dropdown
    # only ever offers the selected client's own types, so picking a type never
    # makes the client invalid — silently dropping it here is what snapped both
    # widgets back to "Any". Adding it keeps the widget valid (no Streamlit
    # crash) and keeps the selection.
    _sel_cname = st.session_state.get(_cname_key)
    if _sel_cname and not str(_sel_cname).startswith("—") and _sel_cname not in _client_options:
        _client_options.append(_sel_cname)
    client_name_choice = st.selectbox(
        "Client name (searchable)",
        _client_options,
        index=0,
        key=_cname_key,
        help=(
            "Search and pick a known client. Selecting one sets the Client type "
            "below to that client's type (all of them if they've booked under "
            "several) and filters Comparable past projects to that same client."
        ),
    )
    client_name_in = "" if client_name_choice.startswith("—") else client_name_choice
    # Remember what the client-name widget settled on this run so the next run
    # can detect a user change (the last-touched-wins check above).
    st.session_state[_prev_cname_key] = client_name_choice

    # --- Client type (scoped to the selected client) ---
    _client_types, _ct_unique = get_client_type_options(
        ml_api_url, selected_service_id, client_name=client_name_in
    )
    if client_name_in and _client_types:
        _customer_options = _client_types
        if _ct_unique:
            _customer_help = (
                f"{client_name_in} books exclusively as **{_client_types[0]}**, "
                "so the client type is set automatically."
            )
        else:
            _customer_help = (
                f"{client_name_in} has booked under {len(_client_types)} client "
                "types — all are listed (most common first). Pick the one that "
                "fits this job."
            )
    else:
        _customer_options = [_ANY] + _client_types
        _customer_help = (
            "Client/customer segment (e.g. 'Lender - CMBS', 'Developer'). Type to "
            "search; the list is alphabetical. Selecting one filters the client "
            "names above. The ML model learned fee patterns by client type, so "
            "this shifts the ML prediction; the rule-based engine ignores it."
        )
    # Preserve the user's explicit type the same way (see Client name above). The
    # deliberate clear when the client changes is handled earlier (pop _ctype_key),
    # so a None selection still re-derives from index 0 below.
    _sel_ctype = st.session_state.get(_ctype_key)
    if _sel_ctype and not str(_sel_ctype).startswith("—") and _sel_ctype not in _customer_options:
        _customer_options.append(_sel_ctype)
    customer_choice = st.selectbox(
        "Client type",
        _customer_options,
        index=0,
        key=_ctype_key,
        help=_customer_help,
    )
    customer_type_in = "" if customer_choice.startswith("—") else customer_choice
    portfolio_size_in = st.number_input(
        "Portfolio size (# of properties)",
        min_value=0,
        value=1,
        step=1,
        help=(
            "Number of properties in the engagement. Larger portfolios may qualify for "
            "a volume adjustment; a value outside the configured tiers flags the job as RFP."
        ),
    )

with col2:
    st.markdown("**Property & size**")
    _secondary_opts = secondary_type_options(
        secondary_types_df, selected_service_id, facility_type_in
    )
    secondary_choice = st.selectbox(
        "Secondary property type",
        ["— Any / not specified —"] + _secondary_opts,
        index=0,
        key=f"secondary_{selected_service_id}_{facility_type_in}",
        help=(
            f"Secondary property types seen historically for {facility_type_in} on this "
            "service (most common first). 'Vacant Land' triggers the Size special case."
        ),
    )
    secondary_in = "" if secondary_choice.startswith("—") else secondary_choice

    if size_eligible and service_uses_building_sf:
        building_area_in = st.number_input(
            "Building area (SF)", min_value=0, value=40000, step=1000,
            help="Gross building size. Drives the size-based fee tier for this service.",
        )
    else:
        building_area_in = 0
        if units_eligible:
            na_field("Building area (SF)", "replaced by Unit Inspection")
        elif is_special_purpose:
            na_field("Building area (SF)", "not used (auto-RFP)")
        else:
            na_field("Building area (SF)", "this service uses Land Ac")

    if size_eligible and service_uses_land_ac:
        land_area_in = st.number_input(
            "Land area (acres)",
            min_value=0.0,
            value=0.0,
            step=0.5,
            help="Site size in acres. Drives the size-based fee tier for this service.",
        )
    else:
        land_area_in = 0.0
        if units_eligible:
            na_field("Land area (acres)", "replaced by Unit Inspection")
        elif is_special_purpose:
            na_field("Land area (acres)", "not used (auto-RFP)")
        else:
            na_field("Land area (acres)", "this service uses Building SF")

    if buildings_eligible and not is_special_purpose:
        bld_category = (
            "# of Buildings 1"
            if facility_type_in in NUMBER_OF_BUILDINGS_FACILITY_TYPE_1
            else "# of Buildings 2"
        )
        number_of_buildings_in = st.number_input(
            f"Number of buildings ({bld_category})",
            min_value=0,
            value=1,
            step=1,
            help=(
                "How many structures are in scope. Multiple buildings add a "
                "per-building adjustment."
            ),
        )
    else:
        number_of_buildings_in = 0
        na_field(
            "Number of buildings",
            "not used (auto-RFP)" if is_special_purpose else "not used for this property type",
        )

    number_of_stories_in = st.number_input(
        "Number of stories", min_value=0, value=2, step=1,
        disabled=is_special_purpose,
        help=(
            "Building height in stories. Taller buildings can add a complexity "
            "adjustment; a value outside the configured tiers flags the job as RFP."
        ),
    )

    if units_eligible:
        total_units_in = st.number_input(
            "Total units", min_value=0, value=0, step=10,
            help=(
                "Unit count for Multi-Family / Seniors Housing. Combined with the "
                "inspection % to size the unit-inspection fee."
            ),
        )
        percent_in = st.selectbox(
            "Percent units to inspect (%)",
            PERCENT_UNITS_TO_INSPECT,
            index=0,
            help="Share of units physically inspected. Higher coverage raises the unit-inspection fee.",
        )
    else:
        total_units_in = 0
        percent_in = 0
        na_field("Total units", "only Multi-Family / Seniors Housing")
        na_field("Percent units to inspect (%)", "only Multi-Family / Seniors Housing")

with col3:
    st.markdown("**Location & risk**")
    country_in = st.selectbox(
        "Country / region",
        ["", "US", "CA"],
        index=0,
        help="Project country. Applies an international/region adjustment when set.",
    )

    travel_options = factors_df[factors_df["category"] == "Travel Difficulty"].copy()
    travel_options["_lvl"] = pd.to_numeric(travel_options["level"], errors="coerce")
    travel_options = travel_options.sort_values("_lvl")
    travel_choices = ["— None —"] + [
        f"{row['level']} — {row['description']}"
        for _, row in travel_options.iterrows()
    ]
    travel_pick = st.selectbox(
        "Travel difficulty", travel_choices, index=0,
        disabled=is_special_purpose,
        help="How hard the site is to reach. Higher difficulty adds a travel surcharge.",
    )
    travel_level: int | None = (
        None if travel_pick.startswith("—") else int(travel_pick.split(" ")[0])
    )

    site_in = st.selectbox(
        "Site complexity",
        [""] + _sorted_descriptions("Site Complexity"),
        index=0,
        disabled=is_special_purpose,
        help="Overall site and scope complexity. Higher complexity adds a surcharge.",
    )

    prior_in = st.selectbox(
        "Prior report",
        [""] + _sorted_descriptions("Prior Report"),
        index=0,
        help=(
            "Whether a recent prior report is available to update. Selecting one "
            "applies the service's prior-report fee adjustment."
        ),
        disabled=is_special_purpose,
    )

    limit_of_liability_in = st.number_input(
        "Limit of liability ($)", min_value=0, value=0, step=50000,
        help="Requested professional liability cap. Higher limits move the fee into a higher liability tier.",
    )


high_margin_only = st.checkbox(
    "Comparables: only show high-margin past projects (> 42%)",
    value=False,
    help=(
        "Filters the Comparable past projects table below to jobs whose service "
        "gross margin exceeded 42%. Toggle, then re-run Calculate to apply."
    ),
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
    land_area=land_area_in,
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


# Call the ML API (best-effort; failure does not block rule-based results)
ml_payload: dict[str, Any] | None = None
ml_error: str | None = None
# The model only covers PCA Equity / ESA / PCA Debt. For other services (Zoning)
# the API has no trained coverage and would return $0, so we skip the call and
# show a clear "not available" notice instead.
ml_supported = selected_service_id in ML_SUPPORTED_SERVICE_IDS
if ml_enabled and ml_supported:
    with st.spinner("Calling ML model..."):
        try:
            ml_payload = call_ml_api(
                ml_api_url,
                order_form_service_id=selected_service_id,
                base_fee=base_fee_in,
                tat=int(tat_in),
                portfolio_size=portfolio_size_in,
                building_area=building_area_in,
                land_area=land_area_in,
                facility_type=facility_type_in,
                secondary_property_type=secondary_in,
                customer_type=customer_type_in,
                limit_of_liability=limit_of_liability_in,
                travel_difficulty_level=travel_level,
                prior_report=prior_in,
                site_complexity=site_in,
                country_code=country_in,
                number_of_stories=number_of_stories_in,
                number_of_buildings=number_of_buildings_in,
                total_units=total_units_in,
                percent_units_to_inspect=percent_in,
                is_rfp=bool(result["is_rfp"]),
            )
            if isinstance(ml_payload, dict) and "error" in ml_payload:
                ml_error = str(ml_payload["error"])
                ml_payload = None
        except Exception as exc:
            ml_error = f"{type(exc).__name__}: {exc}"


# Parsed ML point estimate, reused downstream (e.g. the comparables panel) to
# compare the model against past awarded fees. None when ML is unavailable.
ml_predicted_fee: float | None = None
if ml_payload is not None:
    try:
        _ml_val = float(ml_payload.get("predicted_fee") or 0.0)
        ml_predicted_fee = _ml_val if _ml_val > 0 else None
    except (TypeError, ValueError):
        ml_predicted_fee = None


# Results
st.markdown("---")
st.markdown("### Results")

st.markdown("#### Rule-based estimate")
m1, m2, m3 = st.columns(3)
m1.metric("Base fee", f"${result['base_fee']:,.0f}")
m2.metric("Subtotal", f"${result['subtotal_before_rounding']:,.0f}")
total_display = (
    "RFP" if result["total_fee"] == "RFP" else f"${result['total_fee']:,.0f}"
)
m3.metric("Total fee", total_display)
if result["is_rfp"]:
    rfp_labels = [
        label for col, label in FEE_COLUMNS if result["fees"].get(col) == "RFP"
    ]
    if facility_type_in.strip().lower() == "special purpose":
        reason_short = "the **Special Purpose** property type"
    elif rfp_labels:
        reason_short = "**" + "**, **".join(rfp_labels) + "**"
    else:
        reason_short = "these inputs"
    m3.warning(f"Manual quote needed — because of {reason_short}.")

# Context for the rule-based total: base-fee override note + percentile positioning.
_stats = fee_stats_row(service_id=selected_service_id, property_type=facility_type_in)
if base_fee_in != default_base_fee:
    _median_txt = (
        f" · historical median for {service_label}/{facility_type_in}: "
        f"${_stats['median']:,.0f}"
        if _stats and not pd.isna(_stats.get("median"))
        else ""
    )
    st.caption(
        f"✏️ Manual base-fee override active (default ${default_base_fee:,.0f})"
        f"{_median_txt}."
    )
if isinstance(result["total_fee"], (int, float)):
    _pct = estimate_percentile(float(result["total_fee"]), _stats)
    if _pct is not None:
        st.caption(
            f"📊 This fee sits at about the **{_pct:.0f}th percentile** for "
            f"{service_label}/{facility_type_in} projects (last 3 yrs)."
        )


# ML model row (always rendered when enabled; shows either metrics or a clean error)
if ml_enabled:
    st.markdown("#### ML model prediction")
    if not ml_supported:
        st.info(
            f"ML prediction isn't available for **{service_label}** yet — the model "
            "is trained only on PCA Equity, ESA, and PCA Debt. Use the rule-based "
            "estimate above for this service."
        )
    elif ml_payload is not None:
        ml_predicted = float(ml_payload.get("predicted_fee") or 0.0)
        ml_results = ml_payload.get("results") or {}
        ml_is_rfp = bool(ml_results.get("is_rfp", result["is_rfp"]))

        base = float(result["base_fee"]) or 0.0
        uplift_pct = (ml_predicted / base - 1.0) * 100.0 if base else 0.0

        rule_total_for_delta = (
            float(result["total_fee"]) if isinstance(result["total_fee"], (int, float)) else None
        )

        n2, n3 = st.columns(2)
        n2.metric(
            "Predicted fee",
            f"${ml_predicted:,.0f}",
            delta=f"{uplift_pct:+.0f}% vs. base fee",
            delta_color="off",
            help=(
                "The ML model's single best estimate of the awarded fee for these "
                "inputs, learned from historical projects. The percentage shows how "
                "much higher/lower it is than the base fee."
            ),
        )
        if rule_total_for_delta is not None:
            delta_abs = ml_predicted - rule_total_for_delta
            delta_pct = (delta_abs / rule_total_for_delta * 100.0) if rule_total_for_delta else 0.0
            n3.metric(
                "vs. rule-based total",
                f"${delta_abs:+,.0f}",
                delta=f"{delta_pct:+.1f}%",
                delta_color="off",
                help="How the ML prediction compares to the rule-based total fee above.",
            )
            if abs(delta_pct) >= VARIANCE_WARN_THRESHOLD * 100.0:
                st.warning(
                    f"⚠ The two estimates differ by **{delta_pct:+.0f}%** "
                    f"(rule-based ${rule_total_for_delta:,.0f} vs. ML "
                    f"${ml_predicted:,.0f}). Unusual project — worth a manual review "
                    "before quoting."
                )
        else:
            n3.metric(
                "vs. rule-based total",
                "—",
                help=(
                    "The rule-based engine returned RFP (manual quote), so there is "
                    "no fixed total to compare the ML prediction against."
                ),
            )

        ml_block = ml_payload.get("ml") or {}
        ml_low = ml_block.get("predicted_low")
        ml_high = ml_block.get("predicted_high")
        if ml_high:
            # Confidence from how tight the predicted range is relative to the point
            # estimate (narrower band = more agreement among comparable jobs).
            spread = (float(ml_high) - float(ml_low or 0)) / ml_predicted if ml_predicted else 1.0
            if spread <= 0.35:
                conf_label, conf_icon = "High", "🟢"
            elif spread <= 0.65:
                conf_label, conf_icon = "Medium", "🟡"
            else:
                conf_label, conf_icon = "Low", "🔴"
            with st.container(border=True):
                r1, r2 = st.columns([2, 1])
                r1.metric(
                    "Likely fee range",
                    f"${ml_low:,.0f} – ${ml_high:,.0f}",
                    help="50th–85th percentile of comparable past projects.",
                )
                r2.metric(
                    "Confidence",
                    f"{conf_icon} {conf_label}",
                    help=(
                        "Based on how tight the predicted range is: a narrow band means "
                        "comparable past jobs agree closely, a wide band means more spread."
                    ),
                )
                st.caption(
                    "The predicted fee above is the *most-likely* number. Fees are "
                    "right-skewed, so premium, complex, or busy-period jobs land toward "
                    "the top of this range — use the upper bound as a high estimate."
                )
    elif ml_error:
        st.error(f"ML model unavailable: {ml_error}")
        st.caption(
            "Rule-based result above is unaffected. The PricePilot API may be waking "
            "from sleep — try again in a few seconds."
        )
    else:
        st.info("ML model returned no payload.")
else:
    st.caption("ML model call is disabled in the sidebar.")


# Model accuracy — how well the deployed model did on a hold-out test set, both
# overall and (when available) for the selected service, so users can gauge how
# much to trust the prediction above. Skipped when the model doesn't cover the
# selected service (e.g. Zoning) — there's no prediction to vouch for.
if ml_enabled and ml_supported:
    try:
        model_metrics_df = load_model_metrics()
    except Exception:
        model_metrics_df = None

    overall_w10 = metric_value(model_metrics_df, "model_test", "within_10pct")
    if overall_w10 is not None:
        overall_med = metric_value(model_metrics_df, "model_test", "median_ape_pct")
        with st.expander("How accurate is this model?", expanded=False):
            st.caption(
                "Accuracy on a 20% hold-out test set the model never trained on. "
                '"Within 10%" is the share of past quotes the model predicted to '
                "inside 10% of the real awarded fee."
            )
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Within 10%", f"{overall_w10:.0f}%")
            _w20 = metric_value(model_metrics_df, "model_test", "within_20pct")
            a2.metric("Within 20%", f"{_w20:.0f}%" if _w20 is not None else "—")
            a3.metric(
                "Median error",
                f"{overall_med:.1f}%" if overall_med is not None else "—",
            )
            _r2 = metric_value(model_metrics_df, "model_test", "r2_dollars")
            a4.metric("R² (dollars)", f"{_r2:.2f}" if _r2 is not None else "—")

            svc_scope_name = SERVICE_METRIC_SCOPE.get(selected_service_id)
            svc_scope = f"service_test::{svc_scope_name}" if svc_scope_name else None
            svc_w10 = metric_value(model_metrics_df, svc_scope, "within_10pct") if svc_scope else None
            if svc_w10 is not None:
                st.markdown(f"**For {service_label} specifically**")
                svc_med = metric_value(model_metrics_df, svc_scope, "median_ape_pct")
                svc_w20 = metric_value(model_metrics_df, svc_scope, "within_20pct")
                svc_n = metric_value(model_metrics_df, svc_scope, "n")
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Within 10%", f"{svc_w10:.0f}%")
                b2.metric("Within 20%", f"{svc_w20:.0f}%" if svc_w20 is not None else "—")
                b3.metric(
                    "Median error",
                    f"{svc_med:.1f}%" if svc_med is not None else "—",
                )
                b4.metric("Test jobs", f"{svc_n:,.0f}" if svc_n is not None else "—")
            elif svc_scope_name is None:
                st.caption(
                    f"No service-specific accuracy for {service_label} yet — the "
                    "overall figures above still apply."
                )


# What drives the fee — global feature importance for the deployed ML model, so
# users can see which inputs move the prediction the most (and which barely matter).
# Skipped for services the model doesn't cover (e.g. Zoning).
try:
    importance_df = load_feature_importance() if ml_supported else None
except Exception:
    importance_df = None

if importance_df is not None and not importance_df.empty:
    with st.expander("What drives the fee — ML model feature importance", expanded=False):
        st.caption(
            "How much each input influences the ML model's fee prediction across all "
            "past projects (gain-based importance). Longer bar = bigger effect on the "
            "fee. This is the model's overall ranking, not specific to this quote."
        )
        top = importance_df.head(8)
        st.dataframe(
            pd.DataFrame(
                {"Feature": top["feature_label"], "Importance": top["share_pct"]}
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "Importance": st.column_config.ProgressColumn(
                    "Importance",
                    help="Share of the model's total gain attributed to this feature.",
                    format="%.1f%%",
                    min_value=0.0,
                    max_value=float(top["share_pct"].max() or 1.0),
                ),
            },
        )


# Comparable past projects — real historical jobs most similar to these inputs,
# with their actual awarded fees, so the two estimates above can be sanity-checked
# against reality.
st.markdown("---")
st.markdown("### Comparable past projects")

# Sample-size signal: how much historical data backs this service / property type.
_comp_stats = fee_stats_row(service_id=selected_service_id, property_type=facility_type_in)
if _comp_stats and _comp_stats.get("n"):
    _comp_n = int(_comp_stats["n"])
    if _comp_n < 30:
        st.warning(
            f"⚠ Low sample size: only **{_comp_n}** comparable "
            f"{service_label}/{facility_type_in} projects in the last 3 years — "
            "treat the estimates with extra caution."
        )
    else:
        st.caption(
            f"Backed by **{_comp_n:,}** comparable {service_label}/{facility_type_in} "
            "projects from the last 3 years."
        )


def _fmt_size(row: pd.Series) -> str:
    if service_uses_building_sf:
        v = row.get("building_area")
        return f"{v:,.0f} SF" if pd.notna(v) and v else "—"
    v = row.get("land_acreage")
    return f"{v:,.2f} ac" if pd.notna(v) and v else "—"


def _fmt_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().replace("", "—")


def _fmt_location(row: pd.Series) -> str:
    parts = [
        str(row.get("city") or "").strip(),
        str(row.get("state") or "").strip(),
    ]
    loc = ", ".join(p for p in parts if p)
    return loc or (str(row.get("country") or "").strip() or "—")


def _fmt_client(row: pd.Series) -> str:
    # Prefer the specific client name, fall back to billing company name.
    for key in ("client_name", "company_name"):
        v = str(row.get(key) or "").strip()
        if v:
            return v
    return "—"


def _fmt_service(value: Any) -> str:
    try:
        sid = int(value)
    except (TypeError, ValueError):
        return "—"
    return SERVICE_NAMES.get(sid, f"Service {sid}")


def _comps_to_display(
    frame: pd.DataFrame,
    *,
    show_service: bool = False,
    show_property_type: bool = False,
) -> pd.DataFrame:
    cols: dict[str, Any] = {
        "When": frame["created_month_label"],
    }
    if show_service and "order_form_service_id" in frame.columns:
        cols["Service"] = frame["order_form_service_id"].map(_fmt_service)
    cols.update(
        {
            "Client": frame.apply(_fmt_client, axis=1),
            "Client type": _fmt_text(
                frame["customer_type"].where(
                    frame["customer_type"].fillna("").str.strip() != "",
                    frame["client_type"],
                )
            ),
            "Location": frame.apply(_fmt_location, axis=1),
        }
    )
    if show_property_type and "primary_property_type" in frame.columns:
        cols["Property type"] = _fmt_text(frame["primary_property_type"])
    cols.update(
        {
            "Secondary property type": _fmt_text(frame["secondary_property_type"]),
            "Size": frame.apply(_fmt_size, axis=1),
            "Year built": frame["year_built"].map(
                lambda v: f"{v:,.0f}" if pd.notna(v) and v else "—"
            ),
            "Turnaround": _fmt_text(frame["turn_around_time"]),
            "Limit of liability": _fmt_text(frame["limit_of_liability_tier"]),
            "Buildings": frame["number_of_buildings"].map(
                lambda v: f"{v:,.0f}" if pd.notna(v) and v else "—"
            ),
            "Awarded fee": frame["fee"].map(
                lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
            ),
            "Margin": frame["service_margin"].map(
                lambda v: f"{v * 100:.0f}%" if pd.notna(v) else "—"
            ),
        }
    )
    return pd.DataFrame(cols)


def _render_comps(
    frame: pd.DataFrame,
    *,
    show_compare: bool = True,
    show_service: bool = False,
    show_property_type: bool = False,
    display_limit: int = COMPARABLES_DISPLAY_LIMIT,
) -> None:
    # The fee range/median are computed over the full similarity-ranked pool
    # (up to COMPARABLES_STATS_LIMIT rows), while only the closest `display_limit`
    # rows are listed in the table below.
    fees = pd.to_numeric(frame["fee"], errors="coerce").dropna()
    n = int(fees.shape[0])
    median_fee = float(fees.median()) if n else float("nan")
    p25 = float(fees.quantile(0.25)) if n else float("nan")
    p75 = float(fees.quantile(0.75)) if n else float("nan")

    c_range, c_rule, c_ml = st.columns(3)
    if n:
        c_range.metric(
            f"Comparable fee range (n={n})",
            f"${p25:,.0f} – ${p75:,.0f}",
            help=(
                "25th–75th percentile of awarded fees across the most similar past "
                "projects — a wider pool than the rows shown below."
            ),
        )
        c_range.caption(f"Median ${median_fee:,.0f}")

    if show_compare and n and median_fee:
        rule_total = (
            float(result["total_fee"])
            if isinstance(result["total_fee"], (int, float))
            else None
        )
        if rule_total is not None:
            diff = rule_total - median_fee
            c_rule.metric(
                "Rule-based vs. median",
                f"${diff:+,.0f}",
                delta=f"{diff / median_fee * 100.0:+.0f}%",
                delta_color="off",
                help="How the rule-based total compares to the median of these similar past jobs.",
            )
        if ml_predicted_fee is not None:
            diff_ml = ml_predicted_fee - median_fee
            c_ml.metric(
                "ML vs. median",
                f"${diff_ml:+,.0f}",
                delta=f"{diff_ml / median_fee * 100.0:+.0f}%",
                delta_color="off",
                help="How the ML prediction compares to the median of these similar past jobs.",
            )

    st.dataframe(
        _comps_to_display(
            frame.head(display_limit),
            show_service=show_service,
            show_property_type=show_property_type,
        ),
        width="stretch",
        hide_index=True,
    )


try:
    comps = load_comparables(
        selected_service_id,
        facility_type_in,
        secondary_type=secondary_in,
        client_name=client_name_in,
        building_area=float(building_area_in or 0),
        land_area=float(land_area_in or 0),
        uses_building_sf=bool(service_uses_building_sf),
        min_margin=0.42 if high_margin_only else None,
        limit=COMPARABLES_STATS_LIMIT,
    )
except Exception as exc:
    comps = None
    st.caption(f"Comparable projects unavailable: {exc}")

if comps is not None and not comps.empty:
    # Best case: we have same-service, same-property-type history (client-filtered
    # if a client was picked).
    st.caption(
        f"Real {service_label} / {facility_type_in} projects from the last 3 years, "
        "ranked by how close they are to your inputs"
        + (" (matching secondary property type first)" if secondary_in else "")
        + (f" · same client: {client_name_in}" if client_name_in else "")
        + (" · margin > 42% only" if high_margin_only else "")
        + ". These are actual awarded fees — a reality check on the estimates above."
    )
    _render_comps(comps)
    st.caption(
        "Comparables are ranked by service, property type and size. "
        + ("Results are filtered to the selected client name. " if client_name_in else "")
        + "Client, "
        "location, building age, limit of liability and service gross margin are "
        "shown for context — scope details (deliverables, special conditions) "
        "still vary job to job. Margin is the gross margin earned on that service."
    )

    # When a client filter is active, also surface comparable projects from OTHER
    # clients so users still see the broader market, not just this client's history.
    if client_name_in:
        try:
            other_comps = load_comparables(
                selected_service_id,
                facility_type_in,
                secondary_type=secondary_in,
                exclude_client=client_name_in,
                building_area=float(building_area_in or 0),
                land_area=float(land_area_in or 0),
                uses_building_sf=bool(service_uses_building_sf),
                min_margin=0.42 if high_margin_only else None,
                limit=COMPARABLES_STATS_LIMIT,
            )
        except Exception:
            other_comps = None
        if other_comps is not None and not other_comps.empty:
            st.markdown(
                f"**Other comparable {service_label} / {facility_type_in} projects** "
                "(other clients)"
            )
            _render_comps(other_comps)
            st.caption(
                "Same service and property type from other clients — broader market "
                "context beyond this client's own history, ranked by closeness to your "
                "inputs."
            )
elif client_name_in:
    # Fallback: a client was selected but they have no same-property-type history
    # on this service. Show two related views instead of an empty result —
    # (1) the client's other recent projects, and (2) general comparables for the
    # same service/property type from any client.
    st.info(
        f"No {facility_type_in} projects on record for **{client_name_in}** on "
        f"{service_label}. Showing related history instead."
    )

    try:
        client_comps = load_comparables(
            selected_service_id,
            facility_type_in,
            client_name=client_name_in,
            uses_building_sf=bool(service_uses_building_sf),
            match_service=False,
            match_primary_type=False,
            limit=COMPARABLES_STATS_LIMIT,
        )
    except Exception:
        client_comps = None

    if client_comps is not None and not client_comps.empty:
        st.markdown(f"**Other recent projects for {client_name_in}** (any service / property type)")
        _render_comps(
            client_comps,
            show_compare=False,
            show_service=True,
            show_property_type=True,
            display_limit=8,
        )
        st.caption(
            "All of this client's recent projects, regardless of service or property "
            "type — the **Service** and **Property type** columns show exactly what "
            "each one was. Useful for the relationship and typical fee level even "
            "when there's no direct match for your inputs."
        )

    try:
        general_comps = load_comparables(
            selected_service_id,
            facility_type_in,
            secondary_type=secondary_in,
            building_area=float(building_area_in or 0),
            land_area=float(land_area_in or 0),
            uses_building_sf=bool(service_uses_building_sf),
            min_margin=0.42 if high_margin_only else None,
            limit=COMPARABLES_STATS_LIMIT,
        )
    except Exception:
        general_comps = None

    if general_comps is not None and not general_comps.empty:
        st.markdown(f"**Other comparable {service_label} / {facility_type_in} projects** (any client)")
        _render_comps(general_comps)
        st.caption(
            "Same service and property type from other clients, ranked by closeness "
            "to your inputs — the best size/scope match when this client has no "
            "directly comparable job."
        )

    if (client_comps is None or client_comps.empty) and (
        general_comps is None or general_comps.empty
    ):
        st.caption(
            f"No comparable past projects found for client '{client_name_in}' or for "
            f"{service_label} / {facility_type_in} (not enough recent history)."
        )
else:
    st.caption(
        "No comparable past projects found for this service and property type "
        "(this service may not have enough recent history)."
    )


st.markdown("### Fee breakdown (rule-based)")
st.caption(
    "Shows how each pricing factor adjusts the base fee. The ML model returns a single "
    "predicted total, not a per-category split."
)
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
                "primary_property_type": facility_type_in,
                "secondary_property_type": secondary_in,
                "customer_type": customer_type_in,
                "client_name": client_name_in,
                "country_code": country_in,
                "building_area": building_area_in,
                "land_area": land_area_in,
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
            "result_rule_based": {
                "total_fee": result["total_fee"],
                "is_rfp": result["is_rfp"],
                "subtotal_before_rounding": result["subtotal_before_rounding"],
                "fees": result["fees"],
            },
            "result_ml": (
                {"enabled": False}
                if not ml_enabled
                else {"enabled": True, "error": ml_error, "payload": ml_payload}
            ),
        }
    )


st.markdown("---")
with st.expander("About this tool — version & data provenance", expanded=False):
    st.markdown(f"**App version:** `{APP_VERSION}` · built `{app_build_label()}`")
    st.markdown(f"**Rule engine:** `v{RULE_ENGINE_VERSION}` (official pricing factors)")

    try:
        run_info = load_model_run()
    except Exception:
        run_info = {}
    if run_info:
        run_at = str(run_info.get("run_at") or "")[:10] or "—"
        tag = str(run_info.get("model_tag") or "pricepilot_fee_model")
        try:
            n_feat = int(float(run_info.get("n_features")))
        except (TypeError, ValueError):
            n_feat = None
        try:
            n_rows = int(float(run_info.get("rows_total")))
        except (TypeError, ValueError):
            n_rows = None
        feat_txt = f"{n_feat} features" if n_feat is not None else "—"
        rows_txt = f"{n_rows:,} training rows" if n_rows is not None else "—"
        st.markdown(
            f"**ML model:** `{tag}` · trained {run_at} · {feat_txt} · {rows_txt}"
        )
    else:
        st.markdown("**ML model:** version info unavailable.")

    try:
        freshness = load_data_freshness()
    except Exception:
        freshness = ""
    if freshness:
        st.markdown(f"**Comparable-project history through:** {freshness}")

    st.caption(
        "Two estimates: a rule-based engine (official pricing factors) and the "
        "PricePilot ML model (learned from historical quotes). Accuracy and feature "
        "importance shown above are regenerated automatically on every model retrain."
    )

st.caption(
    f"Service Pricing Tool v{APP_VERSION} · Powered by Keboola Data Apps · "
    "Rule-based engine + PricePilot ML model."
)
