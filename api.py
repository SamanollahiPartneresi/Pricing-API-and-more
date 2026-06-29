"""
PricePilot REST API — rule-based + ML pricing for Copilot Studio / Power Automate.

Keboola Python/JS Data App entrypoint. Listens on port 5000.

GET /?api=true&order_form_service_id=4&base_fee=2400&tat=7&building_area=62000
        &number_of_stories=3&primary_property_type=Office&country_code=US

Note: "primary_property_type" is the preferred field name. "facility_type"
is still accepted as a backward-compatible alias.

POST /quote  (JSON body with the same fields)

Both endpoints run BOTH engines on each request:
    * rule-based: SL_Heaven `Pricing.main_algo` port (see `pricing_engine.py`),
      evaluated against the live `pricing_factors` table in Keboola Storage.
    * ML: LightGBM fee model trained from real historical quotes
      and loaded from a Keboola Storage file tagged `pricepilot_fee_model`.
"""

from __future__ import annotations

import datetime
import io
import math
import json
import os
import time
import warnings
from typing import Any, Dict, Optional

import joblib
import pandas as pd
import requests
from flask import Flask, jsonify, request
from flask.json.provider import DefaultJSONProvider

import pricing_engine
from pricing_engine import (
    FEE_COLUMNS,
    SERVICE_BASE_FEES,
    SERVICE_NAMES,
    breakdown_rows,
    calculate as rule_based_calculate,
)

def _scrub_invalid_json_floats(obj: Any) -> Any:
    """Replace NaN / Infinity (which Python's json emits unquoted) with None,
    so downstream strict JSON parsers (.NET, Power Apps, Copilot Studio) don't
    choke. Walks dicts and lists recursively."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _scrub_invalid_json_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_invalid_json_floats(v) for v in obj]
    return obj


class SafeJSONProvider(DefaultJSONProvider):
    """Flask JSON provider that scrubs NaN/Infinity to null before serializing."""
    def dumps(self, obj: Any, **kwargs: Any) -> str:
        return super().dumps(_scrub_invalid_json_floats(obj), **kwargs)


app = Flask(__name__)
app.json = SafeJSONProvider(app)

INPUT_COLUMNS = [
    "base_fee", "tat", "portfolio_size", "building_area", "land_area",
    "facility_type", "secondary_property_type", "limit_of_liability",
    "travel_difficulty", "prior_report", "site_complexity", "country_code",
    "number_of_stories", "number_of_buildings", "total_units", "percent_units_to_inspect",
]
NUMERIC_COLUMNS = ["base_fee", "tat", "building_area", "number_of_stories"]
CATEGORICAL_COLUMNS = [c for c in INPUT_COLUMNS if c not in NUMERIC_COLUMNS]

# --- Real-data fee model (LightGBM, rule-aligned features) ---
# Trained by the `pricepilot_fee_model_training` transformation on historical
# quotes; loaded from a Storage file tagged FEE_MODEL_TAG. The bundle is a dict:
# {model, features, numeric_features, categorical_features, missing_category, ...}.
FEE_MODEL_TAG = os.environ.get("FEE_MODEL_TAG", "pricepilot_fee_model")

# order_form_service_id -> real service_type_id used by the fee model.
# Zoning (3) is intentionally absent: the model was not trained on it.
ORDER_FORM_TO_SERVICE_TYPE_ID = {1: "353", 2: "301", 4: "346"}

# API uses ISO-ish country codes; the model learned full country names.
COUNTRY_CODE_TO_NAME = {
    "US": "UNITED STATES", "USA": "UNITED STATES",
    "CA": "CANADA", "CAN": "CANADA",
}

PRICING_FACTORS_TABLE_ID = os.environ.get(
    "PRICING_FACTORS_TABLE_ID", "in.c-Pricing_Agent_Input_Data.pricing_factors"
)
PRICING_FACTORS_LOCAL_CSV = os.environ.get(
    "PRICING_FACTORS_LOCAL_CSV", "/data/in/tables/pricing_factors.csv"
)
PRICING_FACTORS_TTL_SECONDS = int(os.environ.get("PRICING_FACTORS_TTL_SECONDS", "600"))
DEFAULT_ORDER_FORM_SERVICE_ID = int(os.environ.get("DEFAULT_ORDER_FORM_SERVICE_ID", "4"))

_MODEL = None
_FEE_MODEL = None
_FACTORS: pd.DataFrame | None = None
_FACTORS_LOADED_AT: float = 0.0


# ML feature prep + model loading


def prep_features(df: pd.DataFrame) -> pd.DataFrame:
    prepared = df.copy()
    for col in CATEGORICAL_COLUMNS:
        prepared[col] = prepared[col].fillna("").astype(str)
    for col in NUMERIC_COLUMNS:
        prepared[col] = pd.to_numeric(prepared[col], errors="coerce").fillna(0)
    return prepared


def load_model():
    """Legacy synthetic-model loader kept for backward compatibility/debugging."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    token = os.environ.get("KBC_TOKEN")
    base_url = os.environ.get("KBC_URL", "https://connection.keboola.com").rstrip("/")
    if not token:
        return None

    files = requests.get(
        f"{base_url}/v2/storage/files",
        headers={"X-StorageApi-Token": token},
        params={"q": "tags:pricepilot_model", "limit": 1},
        timeout=30,
    )
    files.raise_for_status()
    items = files.json()
    if not items:
        return None

    detail = requests.get(
        f"{base_url}/v2/storage/files/{items[0]['id']}",
        headers={"X-StorageApi-Token": token},
        params={"federationToken": 1},
        timeout=30,
    ).json()
    url = detail.get("url")
    if not url:
        return None

    content = requests.get(url, timeout=60).content
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _MODEL = joblib.load(io.BytesIO(content))
    return _MODEL


def load_fee_model():
    """Load the real-data LightGBM fee-model bundle from a Storage file tagged
    FEE_MODEL_TAG. Returns the dict bundle (with keys `model`, `features`, …)
    or None if unavailable."""
    global _FEE_MODEL
    if _FEE_MODEL is not None:
        return _FEE_MODEL

    token = os.environ.get("KBC_TOKEN")
    base_url = os.environ.get("KBC_URL", "https://connection.keboola.com").rstrip("/")
    if not token:
        return None

    files = requests.get(
        f"{base_url}/v2/storage/files",
        headers={"X-StorageApi-Token": token},
        params={"q": f"tags:{FEE_MODEL_TAG}", "limit": 1},
        timeout=30,
    )
    files.raise_for_status()
    items = files.json()
    if not items:
        return None

    detail = requests.get(
        f"{base_url}/v2/storage/files/{items[0]['id']}",
        headers={"X-StorageApi-Token": token},
        params={"federationToken": 1},
        timeout=30,
    ).json()
    url = detail.get("url")
    if not url:
        return None

    content = requests.get(url, timeout=60).content
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _FEE_MODEL = joblib.load(io.BytesIO(content))
    return _FEE_MODEL


# Pricing factors loader (Storage data-preview API, 10-minute cache)


def _normalize_factors_df(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    for col in ("category", "level", "description"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    if "order_form_service_id" in df.columns:
        df["order_form_service_id"] = pd.to_numeric(
            df["order_form_service_id"], errors="coerce"
        ).astype("Int64")
    return df


_FACTORS_BY_SERVICE: Dict[int, pd.DataFrame] = {}
_FACTORS_BY_SERVICE_LOADED_AT: Dict[int, float] = {}


def load_factors_for_service(service_id: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """Load pricing_factors filtered to one service via the data-preview API.

    The data-preview endpoint is capped at ~100 rows, and the largest service
    has 73 factor rows, so a `whereColumn` filter fits. Cached per service for
    PRICING_FACTORS_TTL_SECONDS.
    """
    service_id = int(service_id)
    now = time.time()
    if (
        not force_refresh
        and service_id in _FACTORS_BY_SERVICE
        and (now - _FACTORS_BY_SERVICE_LOADED_AT.get(service_id, 0))
        < PRICING_FACTORS_TTL_SECONDS
    ):
        return _FACTORS_BY_SERVICE[service_id]

    token = os.environ.get("KBC_TOKEN")
    base_url = os.environ.get("KBC_URL", "https://connection.keboola.com").rstrip("/")
    if not token:
        raise RuntimeError("KBC_TOKEN env var is not set; cannot load pricing_factors.")

    response = requests.get(
        f"{base_url}/v2/storage/tables/{PRICING_FACTORS_TABLE_ID}/data-preview",
        headers={"X-StorageApi-Token": token},
        params=[
            ("format", "rfc"),
            ("limit", "100"),
            ("whereColumn", "order_form_service_id"),
            ("whereValues[]", str(service_id)),
            ("whereOperator", "eq"),
        ],
        timeout=30,
    )
    response.raise_for_status()
    df = pd.read_csv(io.StringIO(response.text))
    df = _normalize_factors_df(df)

    _FACTORS_BY_SERVICE[service_id] = df
    _FACTORS_BY_SERVICE_LOADED_AT[service_id] = now
    return df


def load_pricing_factors(force_refresh: bool = False) -> pd.DataFrame:
    """Load factors for all known services (concatenated). Useful when an
    explain endpoint doesn't yet know which service is wanted.
    """
    frames = []
    for sid in SERVICE_NAMES.keys():
        try:
            frames.append(load_factors_for_service(sid, force_refresh=force_refresh))
        except Exception as exc:
            app.logger.warning("Failed loading factors for service %s: %s", sid, exc)
            continue
    if not frames:
        return pd.DataFrame(columns=["category", "level", "description", "value", "order_form_service_id"])
    return pd.concat(frames, ignore_index=True)


# Client directory (client names + client types) via the Keboola Query Service
#
# This is the backend home for the searchable / sortable / cross-filtered client
# and client-type lookups that the Streamlit tool (and Copilot Studio, and any
# future AWS frontend) consume. The big comparable-projects table is aggregated
# to a small per-service directory ONCE, cached in memory, then filtered and
# sorted in process — so dropdowns never re-scan the warehouse per keystroke.

COMPARABLE_PROJECTS_TABLE = os.environ.get(
    "COMPARABLE_PROJECTS_TABLE",
    '"SAPI_10556"."out.c-pricing_ml"."comparable_projects"',
)
CLIENT_DIRECTORY_TTL_SECONDS = int(os.environ.get("CLIENT_DIRECTORY_TTL_SECONDS", "900"))
_QUERY_RESULTS_PAGE_SIZE = 1000

_CLIENT_DIRECTORY: Dict[int, pd.DataFrame] = {}
_CLIENT_DIRECTORY_LOADED_AT: Dict[int, float] = {}


def run_sql(query: str) -> pd.DataFrame:
    """Execute a single SELECT against the project's Keboola workspace via the
    Query Service and return a DataFrame. Mirrors the Streamlit app's data path
    so both share the same warehouse access, but uses `requests` to avoid an
    extra dependency."""
    branch_id = os.environ.get("BRANCH_ID")
    workspace_id = os.environ.get("WORKSPACE_ID")
    token = os.environ.get("KBC_TOKEN")
    kbc_url = os.environ.get("KBC_URL", "https://connection.keboola.com")
    if not (branch_id and workspace_id and token and kbc_url):
        raise RuntimeError(
            "Missing env vars for SQL access: BRANCH_ID, WORKSPACE_ID, KBC_TOKEN, KBC_URL."
        )

    query_service_url = kbc_url.replace("connection.", "query.", 1).rstrip("/") + "/api/v1"
    if token.startswith("Bearer "):
        headers = {"Authorization": token, "Accept": "application/json"}
    else:
        headers = {"X-StorageAPI-Token": token, "Accept": "application/json"}

    submit = requests.post(
        f"{query_service_url}/branches/{branch_id}/workspaces/{workspace_id}/queries",
        json={"statements": [query]},
        headers=headers,
        timeout=60,
    )
    submit.raise_for_status()
    job_id = submit.json().get("queryJobId")
    if not job_id:
        raise RuntimeError("Query Service did not return a job identifier.")

    start_ts = time.monotonic()
    job_info: Dict[str, Any] = {}
    while True:
        status_resp = requests.get(
            f"{query_service_url}/queries/{job_id}", headers=headers, timeout=30
        )
        status_resp.raise_for_status()
        job_info = status_resp.json()
        if job_info.get("status") in {"completed", "failed", "canceled"}:
            break
        if time.monotonic() - start_ts > 120:
            raise TimeoutError(f'Timed out waiting for query "{job_id}".')
        time.sleep(1)

    statements = job_info.get("statements") or []
    if not statements:
        raise RuntimeError("Query Service returned no statements for the query.")
    statement_id = statements[0]["id"]

    columns: list[str] = []
    all_rows: list[list[Any]] = []
    offset = 0
    total_rows = None
    while True:
        results_resp = requests.get(
            f"{query_service_url}/queries/{job_id}/{statement_id}/results",
            headers=headers,
            params={"offset": offset, "pageSize": _QUERY_RESULTS_PAGE_SIZE},
            timeout=60,
        )
        results_resp.raise_for_status()
        results = results_resp.json()
        if results.get("status") != "completed":
            raise ValueError(f"Query error: {results.get('message')}")
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
        if total_rows is None and len(page_rows) < _QUERY_RESULTS_PAGE_SIZE:
            break

    return pd.DataFrame([dict(zip(columns, row)) for row in all_rows])


def load_client_directory(service_id: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """Per-service directory of (client_name, customer_type, n) aggregated from
    the comparable-projects history, cached for CLIENT_DIRECTORY_TTL_SECONDS.
    Client type is NOT unique per client, so every (client, type) pair is kept;
    the lookup helpers decide how to collapse/sort them."""
    service_id = int(service_id)
    now = time.time()
    if (
        not force_refresh
        and service_id in _CLIENT_DIRECTORY
        and (now - _CLIENT_DIRECTORY_LOADED_AT.get(service_id, 0)) < CLIENT_DIRECTORY_TTL_SECONDS
    ):
        return _CLIENT_DIRECTORY[service_id]

    client_expr = (
        'COALESCE(NULLIF(TRIM("client_name"), \'\'), NULLIF(TRIM("company_name"), \'\'))'
    )
    query = (
        f'SELECT {client_expr} AS "client_name", TRIM("customer_type") AS "customer_type", '
        'COUNT(*) AS "n" '
        f"FROM {COMPARABLE_PROJECTS_TABLE} "
        f"WHERE {client_expr} IS NOT NULL "
        "AND \"customer_type\" IS NOT NULL AND TRIM(\"customer_type\") <> '' "
        f"AND \"order_form_service_id\" = {service_id} "
        "GROUP BY 1, 2"
    )
    df = run_sql(query)
    if df.empty:
        df = pd.DataFrame(columns=["client_name", "customer_type", "n"])
    else:
        df["client_name"] = df["client_name"].astype(str).str.strip()
        df["customer_type"] = df["customer_type"].astype(str).str.strip()
        df["n"] = pd.to_numeric(df["n"], errors="coerce").fillna(0).astype(int)
        df = df[(df["client_name"] != "") & (df["customer_type"] != "")]

    _CLIENT_DIRECTORY[service_id] = df
    _CLIENT_DIRECTORY_LOADED_AT[service_id] = now
    return df


def query_clients(
    service_id: int, *, search: str = "", client_type: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    """Searchable, alphabetically-sorted client names for a service. When
    `client_type` is given (the 'vice versa' direction), only clients who have
    booked under that type are returned."""
    df = load_client_directory(service_id)
    if df.empty:
        return []
    if client_type:
        df = df[df["customer_type"].str.lower() == client_type.strip().lower()]
    grouped = df.groupby("client_name", as_index=False)["n"].sum()
    if search:
        needle = search.strip().lower()
        grouped = grouped[grouped["client_name"].str.lower().str.contains(needle, regex=False)]
    grouped = grouped.sort_values("client_name", key=lambda c: c.str.lower())
    rows = [{"name": r["client_name"], "n": int(r["n"])} for _, r in grouped.iterrows()]
    return rows[:limit] if limit and limit > 0 else rows


def query_client_types(
    service_id: int, *, search: str = "", client_name: str = "", limit: int = 50
) -> tuple[list[dict[str, Any]], bool]:
    """Searchable client types for a service. When `client_name` is given, the
    list is scoped to that client's own type(s) — sorted most-common first so the
    primary type leads — and the second return value reports whether that client
    maps to a single (unique) type. Without a client, all service types are
    returned, sorted alphabetically. Returns (rows, is_unique_for_client)."""
    df = load_client_directory(service_id)
    if df.empty:
        return [], False
    scoped = bool(client_name)
    if client_name:
        df = df[df["client_name"].str.lower() == client_name.strip().lower()]
    grouped = df.groupby("customer_type", as_index=False)["n"].sum()
    if search:
        needle = search.strip().lower()
        grouped = grouped[grouped["customer_type"].str.lower().str.contains(needle, regex=False)]
    if scoped:
        grouped = grouped.sort_values("n", ascending=False)
    else:
        grouped = grouped.sort_values("customer_type", key=lambda c: c.str.lower())
    rows = [{"name": r["customer_type"], "n": int(r["n"])} for _, r in grouped.iterrows()]
    is_unique = scoped and len(rows) == 1
    return (rows[:limit] if limit and limit > 0 else rows), is_unique


def _sql_quote(value: str) -> str:
    """Escape single quotes for safe inlining in a SQL string literal."""
    return str(value).replace("'", "''")


# Same name expression the client directory and the Streamlit comparables use, so
# a client picked from the directory matches its own history exactly.
_CLIENT_NAME_EXPR = (
    'COALESCE(NULLIF(TRIM("client_name"), \'\'), NULLIF(TRIM("company_name"), \'\'))'
)


def _client_history_rows(
    client_name: str, *, service_id: Optional[int], limit: int
) -> list[dict[str, Any]]:
    """A client's most recent past projects (newest first), with their real
    awarded fees. Restricted to `service_id` when given, else across all services.
    Reads the comparable-projects history directly via the Query Service."""
    where = [f"{_CLIENT_NAME_EXPR} = '{_sql_quote(client_name)}'"]
    if service_id is not None:
        where.append(f'"order_form_service_id" = {int(service_id)}')
    sql = (
        'SELECT "order_form_service_id", "primary_property_type", '
        '"secondary_property_type", "customer_type", "city", "state", '
        '"fee", "service_margin", "created_month_label" '
        f"FROM {COMPARABLE_PROJECTS_TABLE} "
        f"WHERE {' AND '.join(where)} "
        'ORDER BY "created_month_label" DESC '
        f"LIMIT {max(1, int(limit))}"
    )
    df = run_sql(sql)
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        sid = _to_int(r.get("order_form_service_id"), 0)
        fee = pd.to_numeric(r.get("fee"), errors="coerce")
        margin = pd.to_numeric(r.get("service_margin"), errors="coerce")
        loc = ", ".join(
            p
            for p in [str(r.get("city") or "").strip(), str(r.get("state") or "").strip()]
            if p
        )
        out.append({
            "order_form_service_id": sid,
            "service_name": SERVICE_NAMES.get(sid, f"Service {sid}"),
            "when": (str(r.get("created_month_label") or "").strip() or None),
            "primary_property_type": (str(r.get("primary_property_type") or "").strip() or None),
            "secondary_property_type": (str(r.get("secondary_property_type") or "").strip() or None),
            "customer_type": (str(r.get("customer_type") or "").strip() or None),
            "location": loc or None,
            "fee": None if pd.isna(fee) else float(fee),
            "service_margin": None if pd.isna(margin) else float(margin),
        })
    return out


def query_client_history(
    client_name: str, *, service_id: Optional[int] = None, limit: int = 5
) -> dict[str, Any]:
    """A client's recent awarded-fee history: their most recent projects on the
    selected service (when one is given), plus their most recent projects across
    ALL services — so a recently-active client always shows even when their latest
    work was on a different service. Actual awarded fees, last 3 years, newest
    first."""
    client_name = (client_name or "").strip()
    if not client_name:
        return {"client_name": "", "service_history": [], "recent_any_service": []}
    service_history = (
        _client_history_rows(client_name, service_id=service_id, limit=limit)
        if service_id is not None
        else []
    )
    recent_any = _client_history_rows(client_name, service_id=None, limit=3)
    return {
        "client_name": client_name,
        "service_history": service_history,
        "recent_any_service": recent_any,
    }


# Input parsing


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def parse_input_row(params: dict[str, Any]) -> dict[str, Any]:
    """Normalize incoming API params into the shared input shape used by
    both engines. Strings stay strings (the ML model expects strings for
    its categorical columns); numeric fields become numbers."""
    return {
        "pricing_id": _to_int(params.get("pricing_id"), 1),
        "order_form_service_id": _to_int(
            params.get("order_form_service_id"), DEFAULT_ORDER_FORM_SERVICE_ID
        ),
        "base_fee": _to_float(params.get("base_fee"), 0.0),
        "tat": _to_int(params.get("tat"), 0),
        "portfolio_size": _to_str(params.get("portfolio_size")),
        "building_area": _to_float(params.get("building_area"), 0.0),
        "land_area": _to_str(params.get("land_area")),
        # "primary_property_type" is the preferred, user-facing field name;
        # "facility_type" is accepted as a backward-compatible alias.
        "facility_type": _to_str(
            params.get("primary_property_type") or params.get("facility_type"),
            "Industrial",
        ),
        "secondary_property_type": _to_str(params.get("secondary_property_type")),
        # Client/customer-type bucket (e.g. "Lender - CMBS", "Developer"). Maps to
        # the model's `customer_type` categorical feature. Accept either param name.
        "customer_type": _to_str(params.get("customer_type") or params.get("client_type")),
        "limit_of_liability": _to_str(params.get("limit_of_liability")),
        "travel_difficulty": _to_str(params.get("travel_difficulty")),
        "prior_report": _to_str(params.get("prior_report")),
        "site_complexity": _to_str(params.get("site_complexity")),
        "country_code": _to_str(params.get("country_code"), "US"),
        "number_of_stories": _to_int(params.get("number_of_stories"), 0),
        "number_of_buildings": _to_str(params.get("number_of_buildings")),
        "total_units": _to_str(params.get("total_units")),
        "percent_units_to_inspect": _to_str(params.get("percent_units_to_inspect")),
        "is_rfp_hint": _to_bool(params.get("is_rfp"), False),
    }


# Rule-based runner — adapts the shared input row to pricing_engine.calculate()


def run_rule_based(input_row: dict[str, Any]) -> dict[str, Any]:
    """Filter pricing_factors to the requested service and run the canonical
    `Pricing.main_algo` port. Returns the structured dict produced by
    `pricing_engine.calculate()` plus an `order_form_service_id`/`base_fee_default`
    summary that the API layer surfaces in the response."""
    service_id = int(input_row["order_form_service_id"])
    factors_df = load_factors_for_service(service_id).copy()

    base_fee = float(input_row["base_fee"])
    if base_fee <= 0:
        base_fee = float(SERVICE_BASE_FEES.get(service_id, 5000.0))

    result = rule_based_calculate(
        factors_df,
        base_fee=base_fee,
        tat=int(input_row["tat"]),
        portfolio_size=_to_float(input_row["portfolio_size"], 0.0),
        building_area=float(input_row["building_area"]),
        land_area=_to_float(input_row["land_area"], 0.0),
        facility_type=input_row["facility_type"],
        secondary_property_type=input_row["secondary_property_type"],
        limit_of_liability=_to_float(input_row["limit_of_liability"], 0.0),
        travel_difficulty_level=(
            _to_int(input_row["travel_difficulty"], 0) or None
        ),
        prior_report=input_row["prior_report"] or None,
        site_complexity=input_row["site_complexity"] or None,
        country_code=input_row["country_code"] or None,
        number_of_stories=float(input_row["number_of_stories"]),
        number_of_buildings=_to_float(input_row["number_of_buildings"], 0.0),
        total_units=_to_float(input_row["total_units"], 0.0),
        percent_units_to_inspect=_to_float(input_row["percent_units_to_inspect"], 0.0),
        always_include_tat=False,
    )

    breakdown = breakdown_rows(result)
    fees_clean = {
        k: ("RFP" if v == "RFP" else int(v)) if isinstance(v, (int, str)) else v
        for k, v in result["fees"].items()
    }
    tat_totals = result.get("tat_totals")

    return {
        "order_form_service_id": service_id,
        "service_name": SERVICE_NAMES.get(service_id),
        "base_fee": base_fee,
        "base_fee_default_for_service": SERVICE_BASE_FEES.get(service_id),
        "base_fee_overridden": base_fee != float(SERVICE_BASE_FEES.get(service_id, base_fee)),
        "subtotal_before_rounding": result["subtotal_before_rounding"],
        "total_fee": result["total_fee"],
        "is_rfp": bool(result["is_rfp"]),
        "fees": fees_clean,
        "breakdown": breakdown,
        "tat_totals_by_day": tat_totals,
        "factors_loaded_count": int(len(factors_df)),
    }


# ML runner


def _pos_or_nan(value: Any) -> float:
    """Numeric value if > 0, else NaN. Mirrors training, where 'not provided'
    size/count fields were NaN (not 0)."""
    f = _to_float(value, 0.0)
    return f if f > 0 else float("nan")


def run_ml(input_row: dict[str, Any], is_rfp: bool) -> dict[str, Any]:
    """Predict the fee with the real-data LightGBM model, mapping the rule-based
    inputs to the model's rule-aligned feature schema. The model predicts
    log(fee); we exponentiate back to dollars. Raises with a descriptive error
    (e.g. for Zoning, which the model was not trained on)."""
    bundle = load_fee_model()
    if bundle is None:
        raise RuntimeError(
            "ML fee model not loaded. Run pricepilot_fee_model_training and "
            "tag the joblib file with `pricepilot_fee_model`."
        )

    model = bundle["model"]
    features = bundle["features"]
    numeric = bundle["numeric_features"]
    categorical = bundle["categorical_features"]
    missing = bundle.get("missing_category", "__missing__")

    sid = int(input_row["order_form_service_id"])
    service_type_id = ORDER_FORM_TO_SERVICE_TYPE_ID.get(sid)
    if service_type_id is None:
        raise RuntimeError(
            f"ML fee model was not trained for {SERVICE_NAMES.get(sid, f'service {sid}')}; "
            "rule-based pricing is the source of record for this service."
        )

    base_fee = float(input_row["base_fee"])
    if base_fee <= 0:
        base_fee = float(SERVICE_BASE_FEES.get(sid, 0.0))

    now = datetime.datetime.utcnow()
    country = COUNTRY_CODE_TO_NAME.get(
        str(input_row["country_code"]).strip().upper(),
        str(input_row["country_code"]).strip(),
    )

    # One value per model feature (rule-aligned). The API's `land_area` field
    # carries acreage (rule engine 'Land Ac'), which maps to the model's
    # `land_acreage`. `created_month` is the single Time Period / busy-level
    # signal. Build only the keys the model bundle declares; `X = X[features]`
    # below still guards against drift if the bundle's feature set changes.
    raw = {
        # base_fee is NOT a model feature (service_type_id carries that signal);
        # it is kept above only as the multiplier denominator.
        "turn_around_time": _pos_or_nan(input_row["tat"]),
        "building_area": _pos_or_nan(input_row["building_area"]),
        "land_acreage": _pos_or_nan(input_row["land_area"]),
        "total_units": _pos_or_nan(input_row["total_units"]),
        "pct_units_inspect": _pos_or_nan(input_row["percent_units_to_inspect"]),
        "number_of_stories": _pos_or_nan(input_row["number_of_stories"]),
        "number_of_buildings": _pos_or_nan(input_row["number_of_buildings"]),
        "created_month": now.month,
        "service_type_id": service_type_id,
        "primary_property_type": _to_str(input_row["facility_type"]) or None,
        "secondary_property_type": _to_str(input_row["secondary_property_type"]) or None,
        "prior_report": _to_str(input_row["prior_report"]) or None,
        "site_complexity": _to_str(input_row["site_complexity"]) or None,
        "country": country or None,
        "customer_type": _to_str(input_row["customer_type"]) or None,
    }

    X = pd.DataFrame([raw])
    # Robust to bundle/dict drift: any feature the model expects but we didn't
    # populate falls back to missing rather than raising KeyError.
    for col in numeric:
        if col not in X.columns:
            X[col] = float("nan")
        X[col] = pd.to_numeric(X[col], errors="coerce")
    for col in categorical:
        if col not in X.columns:
            X[col] = missing
        s = X[col].astype("string").fillna(missing).replace("", missing)
        X[col] = s.astype("category")
    X = X[features]

    pred_log = float(model.predict(X)[0])
    predicted = math.exp(pred_log)
    base_for_mult = base_fee or 1.0

    def _round50(value: float) -> int:
        return int(round(value / 50.0) * 50)

    out: dict[str, Any] = {
        "predicted_fee": _round50(predicted),
        "predicted_fee_raw": round(predicted, 2),
        "predicted_multiplier": round(predicted / base_for_mult, 4),
        "is_rfp": bool(is_rfp),
    }

    # Predicted RANGE from the bundle's quantile models (p50 / p85). The point
    # estimate above is the most-likely fee; on right-skewed / premium jobs it
    # reads low, so the range shows how high the fee realistically goes.
    qmodels = bundle.get("quantile_models") or {}
    p50 = math.exp(float(qmodels["0.5"].predict(X)[0])) if "0.5" in qmodels else None
    p85 = math.exp(float(qmodels["0.85"].predict(X)[0])) if "0.85" in qmodels else None
    if p85 is not None:
        # Keep the band monotone even if the independently-fit quantiles cross.
        low_raw = min(v for v in (p50, predicted) if v is not None)
        high_raw = max(v for v in (p85, p50, predicted) if v is not None)
        out["predicted_low"] = _round50(low_raw)
        out["predicted_high"] = _round50(high_raw)
        out["predicted_low_raw"] = round(low_raw, 2)
        out["predicted_high_raw"] = round(high_raw, 2)
        out["range_quantiles"] = bundle.get("quantiles", [0.5, 0.85])
    return out


# Response assembly + comparison


def assemble_response(input_row: dict[str, Any]) -> dict[str, Any]:
    """Run both engines and combine results."""
    rule_based = run_rule_based(input_row)

    ml_result: dict[str, Any] | None = None
    ml_error: str | None = None
    try:
        ml_result = run_ml(input_row, is_rfp=rule_based["is_rfp"] or input_row["is_rfp_hint"])
    except Exception as exc:
        ml_error = f"{type(exc).__name__}: {exc}"

    rule_total = rule_based["total_fee"]
    ml_total = ml_result["predicted_fee"] if ml_result else None

    comparison: dict[str, Any] = {
        "rule_based_total": rule_total,
        "ml_total": ml_total,
        "ml_low": ml_result.get("predicted_low") if ml_result else None,
        "ml_high": ml_result.get("predicted_high") if ml_result else None,
        "delta_abs": None,
        "delta_pct": None,
        "rule_based_is_rfp": rule_based["is_rfp"],
    }
    if isinstance(rule_total, (int, float)) and isinstance(ml_total, (int, float)):
        rule_total_f = float(rule_total)
        ml_total_f = float(ml_total)
        comparison["delta_abs"] = ml_total_f - rule_total_f
        if rule_total_f:
            comparison["delta_pct"] = round((ml_total_f - rule_total_f) / rule_total_f * 100.0, 2)

    backcompat_results = {
        "pricing_id": input_row["pricing_id"],
        "base_fee": float(input_row["base_fee"]),
        "tat_fee": rule_based["fees"].get("tat_fee"),
        "portfolio_fee": rule_based["fees"].get("portfolio_fee"),
        "size_fee": rule_based["fees"].get("size_fee"),
        "units_fee": rule_based["fees"].get("units_fee"),
        "buildings_fee": rule_based["fees"].get("buildings_fee"),
        "stories_fee": rule_based["fees"].get("stories_fee"),
        "travel_difficulty_fee": rule_based["fees"].get("travel_difficulty_fee"),
        "prior_report_fee": rule_based["fees"].get("prior_report_fee"),
        "site_complexity_fee": rule_based["fees"].get("site_complexity_fee"),
        "international_fee": rule_based["fees"].get("international_fee"),
        "limit_of_liability_fee": rule_based["fees"].get("limit_of_liability_fee"),
        "total_fee": rule_based["total_fee"],
        "is_rfp": rule_based["is_rfp"],
    }

    payload: dict[str, Any] = {
        "inputs": input_row,
        "rule_based": rule_based,
        "ml": ml_result,
        "ml_error": ml_error,
        "comparison": comparison,
        "results": backcompat_results,
    }
    if ml_result is not None:
        payload["predicted_fee"] = ml_result["predicted_fee"]
        payload["predicted_multiplier"] = ml_result["predicted_multiplier"]
    return payload


# Flask routes


def _gather_params() -> dict[str, Any]:
    """Accept JSON body (POST) or query string (GET); merge JSON over query."""
    params: dict[str, Any] = {}
    if request.args:
        params.update({k: request.args.get(k) for k in request.args if k != "api"})
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict):
            params.update(body)
    return params


@app.route("/", methods=["GET", "POST"])
def root():
    if request.method == "GET" and not request.args:
        return jsonify({
            "status": "running",
            "endpoints": {
                "GET /?api=true&...": "rule-based + ML quote via query string",
                "POST /quote": "rule-based + ML quote via JSON body",
                "GET /health": "liveness check",
                "GET /ready": "readiness check (factors + ML availability)",
                "GET /services": "list of services with default base fees",
                "GET /pricing-factors?service_id=4": "factor rules for a service",
                "GET /clients?service_id=4&search=&client_type=": "searchable client names (optionally filtered by client type)",
                "GET /client-types?service_id=4&search=&client_name=": "searchable client types (scoped to a client when client_name is given)",
            },
            "version": "2.0",
        })

    if request.method == "GET" and request.args.get("api", "").lower() != "true":
        return jsonify({
            "error": "Missing api=true. Example: /?api=true&order_form_service_id=4&base_fee=2400&tat=7&primary_property_type=Office",
        }), 400

    try:
        params = _gather_params()
        input_row = parse_input_row(params)
        return jsonify(assemble_response(input_row))
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/quote", methods=["POST"])
def quote():
    try:
        params = _gather_params()
        input_row = parse_input_row(params)
        return jsonify(assemble_response(input_row))
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/health", methods=["GET"])
def health():
    """Lightweight liveness check — does not load the ML model."""
    return jsonify({"status": "ok", "service": "pricepilot-api"})


@app.route("/ready", methods=["GET"])
def ready():
    """Readiness check: verify factors load and report ML model availability."""
    checks: Dict[str, Any] = {}
    try:
        factors = load_factors_for_service(DEFAULT_ORDER_FORM_SERVICE_ID)
        checks["pricing_factors"] = {
            "ok": True,
            "service_id": DEFAULT_ORDER_FORM_SERVICE_ID,
            "row_count": int(len(factors)),
            "table_id": PRICING_FACTORS_TABLE_ID,
        }
    except Exception as exc:
        checks["pricing_factors"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        bundle = load_fee_model()
        checks["fee_model"] = {
            "ok": bundle is not None,
            "tag": FEE_MODEL_TAG,
            "loaded": bundle is not None,
            "features_count": len(bundle.get("features", [])) if bundle else 0,
        }
    except Exception as exc:
        checks["fee_model"] = {"ok": False, "tag": FEE_MODEL_TAG, "error": f"{type(exc).__name__}: {exc}"}

    ready_ok = checks.get("pricing_factors", {}).get("ok", False)
    status_code = 200 if ready_ok else 503
    return jsonify({"ready": ready_ok, "checks": checks}), status_code


@app.route("/services", methods=["GET"])
def services():
    """List the services with default base fees and factor counts (handy for
    Copilot Studio dropdowns)."""
    try:
        factors = load_pricing_factors()
        counts = factors.groupby("order_form_service_id").size().to_dict()
    except Exception:
        counts = {}
    out = []
    for sid, name in sorted(SERVICE_NAMES.items()):
        out.append({
            "order_form_service_id": sid,
            "name": name,
            "base_fee_default": SERVICE_BASE_FEES.get(sid),
            "factor_row_count": int(counts.get(sid, 0)) if counts else None,
        })
    return jsonify({"services": out})


@app.route("/debug/ml-info", methods=["GET"])
def debug_ml_info():
    """Return introspection on the live fee-model bundle used by /quote."""
    import traceback

    try:
        try:
            bundle = load_fee_model()
        except Exception as exc:
            return jsonify({"error": f"load_fee_model failed: {type(exc).__name__}: {exc}"}), 500
        if bundle is None:
            return jsonify(
                {"error": f"Fee model bundle failed to load (check token and `{FEE_MODEL_TAG}` tag)."}
            ), 500

        model = bundle.get("model")
        quantile_models = bundle.get("quantile_models") or {}
        info: Dict[str, Any] = {
            "fee_model_tag": FEE_MODEL_TAG,
            "bundle_keys": sorted(list(bundle.keys())),
            "model_class": type(model).__name__ if model is not None else None,
            "model_module": type(model).__module__ if model is not None else None,
            "features_count": len(bundle.get("features", [])),
            "features_sample": list(bundle.get("features", []))[:30],
            "numeric_features_count": len(bundle.get("numeric_features", [])),
            "categorical_features_count": len(bundle.get("categorical_features", [])),
            "missing_category": bundle.get("missing_category"),
            "quantiles": bundle.get("quantiles", []),
            "quantile_models": sorted(list(quantile_models.keys())),
        }

        return jsonify(info)
    except Exception as exc:
        return jsonify({
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc().splitlines()[-15:],
        }), 500


@app.route("/debug/files", methods=["GET"])
def debug_files():
    """List files under /data so we can verify input mappings are present."""
    out: Dict[str, Any] = {
        "cwd": os.getcwd(),
        "local_csv_path": PRICING_FACTORS_LOCAL_CSV,
        "local_csv_exists": os.path.exists(PRICING_FACTORS_LOCAL_CSV),
    }
    for root in ("/data", "/data/in", "/data/in/tables", "/data/in/files"):
        try:
            entries = []
            if os.path.isdir(root):
                for name in sorted(os.listdir(root)):
                    full = os.path.join(root, name)
                    entries.append({
                        "name": name,
                        "is_dir": os.path.isdir(full),
                        "size": os.path.getsize(full) if os.path.isfile(full) else None,
                    })
            out[root] = entries if os.path.isdir(root) else "(missing)"
        except Exception as exc:
            out[root] = f"error: {exc}"
    try:
        with open("/data/config.json", "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        out["config_keys"] = list(cfg.keys())
        out["config_storage"] = cfg.get("storage")
        params = cfg.get("parameters") or cfg.get("image_parameters") or {}
        out["config_param_keys"] = list(params.keys()) if isinstance(params, dict) else None
    except Exception as exc:
        out["config_error"] = f"{type(exc).__name__}: {exc}"
    return jsonify(out)


@app.route("/clients", methods=["GET"])
def clients_endpoint():
    """Searchable, sorted client names for a service. Pass `client_type` to
    restrict to clients who have booked under that type (the reverse of
    /client-types' client scoping). Powers the client dropdown in any frontend."""
    try:
        service_id = int(request.args.get("service_id", DEFAULT_ORDER_FORM_SERVICE_ID))
        search = _to_str(request.args.get("search"))
        client_type = _to_str(
            request.args.get("client_type") or request.args.get("customer_type")
        )
        limit = _to_int(request.args.get("limit"), 50)
        rows = query_clients(service_id, search=search, client_type=client_type, limit=limit)
        return jsonify({
            "order_form_service_id": service_id,
            "service_name": SERVICE_NAMES.get(service_id),
            "client_type": client_type or None,
            "search": search or None,
            "count": len(rows),
            "clients": rows,
        })
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/client-types", methods=["GET"])
def client_types_endpoint():
    """Searchable, sorted client types for a service. Pass `client_name` to scope
    to that client's own type(s): one type means it's set automatically, several
    means all are returned (most common first) since client type is not unique
    per client. `is_unique_for_client` reflects that when a client is given."""
    try:
        service_id = int(request.args.get("service_id", DEFAULT_ORDER_FORM_SERVICE_ID))
        search = _to_str(request.args.get("search"))
        client_name = _to_str(request.args.get("client_name"))
        limit = _to_int(request.args.get("limit"), 50)
        rows, is_unique = query_client_types(
            service_id, search=search, client_name=client_name, limit=limit
        )
        return jsonify({
            "order_form_service_id": service_id,
            "service_name": SERVICE_NAMES.get(service_id),
            "client_name": client_name or None,
            "search": search or None,
            "is_unique_for_client": is_unique if client_name else None,
            "count": len(rows),
            "client_types": rows,
        })
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/client-history", methods=["GET"])
def client_history_endpoint():
    """A client's recent awarded-fee history — actual past project fees, to sanity-
    check a quote against what the client has paid before. Pass `client_name`
    (required) and optionally `service_id`: returns their most recent projects on
    that service plus their most recent projects across ALL services (newest
    first)."""
    try:
        client_name = _to_str(request.args.get("client_name"))
        if not client_name:
            return jsonify({"error": "client_name is required."}), 400
        service_id_raw = request.args.get("service_id")
        service_id = (
            int(service_id_raw) if service_id_raw not in (None, "") else None
        )
        limit = _to_int(request.args.get("limit"), 5)
        result = query_client_history(client_name, service_id=service_id, limit=limit)
        return jsonify({
            "client_name": result["client_name"],
            "order_form_service_id": service_id,
            "service_name": SERVICE_NAMES.get(service_id) if service_id is not None else None,
            "service_history": result["service_history"],
            "recent_any_service": result["recent_any_service"],
            "service_history_count": len(result["service_history"]),
            "recent_any_count": len(result["recent_any_service"]),
        })
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/pricing-factors", methods=["GET"])
def pricing_factors_endpoint():
    """Return the raw factor rules for a service (debug/explain support)."""
    try:
        service_id = int(request.args.get("service_id", DEFAULT_ORDER_FORM_SERVICE_ID))
        factors = load_factors_for_service(service_id)
        wanted_cols = [c for c in ("category", "level", "description", "value") if c in factors.columns]
        rows = (
            factors[wanted_cols].fillna("").astype(str).to_dict(orient="records")
            if wanted_cols
            else []
        )
        return jsonify({
            "order_form_service_id": service_id,
            "service_name": SERVICE_NAMES.get(service_id),
            "row_count": len(rows),
            "rows": rows,
        })
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
