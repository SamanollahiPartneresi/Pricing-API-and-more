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
    * ML: scikit-learn model trained by `pricing_model_training_transformation`
      and loaded from a Keboola Storage file tagged `pricepilot_model`.
"""

from __future__ import annotations

import datetime
import io
import math
import json
import os
import time
import warnings
from typing import Any, Dict

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
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["category", "level", "description", "value", "order_form_service_id"])
    return pd.concat(frames, ignore_index=True)


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
                "GET /services": "list of services with default base fees",
                "GET /pricing-factors?service_id=4": "factor rules for a service",
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
    """Return introspection on the loaded joblib model: type, feature names,
    expected n_features, pipeline steps if any. Helps diagnose why the model
    predicts near-constant values regardless of inputs."""
    import traceback

    try:
        try:
            model = load_model()
        except Exception as exc:
            return jsonify({"error": f"load_model failed: {type(exc).__name__}: {exc}"}), 500
        if model is None:
            return jsonify({"error": "Model failed to load (no token or no tagged file)."}), 500

        info: Dict[str, Any] = {
            "model_class": type(model).__name__,
            "model_module": type(model).__module__,
            "is_pipeline": False,
            "top_level_attrs": [a for a in dir(model) if not a.startswith("_")][:50],
        }

        try:
            fni = getattr(model, "feature_names_in_", None)
            if fni is not None:
                info["feature_names_in"] = [str(x) for x in fni]
        except Exception as exc:
            info["feature_names_in_error"] = f"{type(exc).__name__}: {exc}"

        try:
            n = getattr(model, "n_features_in_", None)
            if n is not None:
                info["n_features_in"] = int(n)
        except Exception as exc:
            info["n_features_in_error"] = f"{type(exc).__name__}: {exc}"

        if hasattr(model, "steps"):
            info["is_pipeline"] = True
            info["pipeline_steps"] = []
            for name, step in model.steps:
                step_info: Dict[str, Any] = {
                    "name": str(name),
                    "class": type(step).__name__,
                    "module": type(step).__module__,
                }
                try:
                    fni = getattr(step, "feature_names_in_", None)
                    if fni is not None:
                        step_info["feature_names_in"] = [str(x) for x in fni]
                except Exception as exc:
                    step_info["feature_names_in_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    if hasattr(step, "get_feature_names_out"):
                        out = step.get_feature_names_out()
                        step_info["feature_names_out_count"] = int(len(out))
                        step_info["feature_names_out_sample"] = [str(x) for x in list(out)[:30]]
                except Exception as exc:
                    step_info["feature_names_out_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    cats = getattr(step, "categories_", None)
                    if cats is not None:
                        step_info["categories_per_column"] = [
                            [str(x) for x in list(c)[:20]] for c in cats
                        ]
                except Exception as exc:
                    step_info["categories_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    n = getattr(step, "n_features_in_", None)
                    if n is not None:
                        step_info["n_features_in"] = int(n)
                except Exception:
                    pass
                try:
                    fi = getattr(step, "feature_importances_", None)
                    if fi is not None:
                        step_info["feature_importances"] = [round(float(x), 5) for x in fi]
                except Exception as exc:
                    step_info["feature_importances_error"] = f"{type(exc).__name__}: {exc}"
                info["pipeline_steps"].append(step_info)

        try:
            fi = getattr(model, "feature_importances_", None)
            if fi is not None and "feature_importances" not in info:
                info["feature_importances"] = [round(float(x), 5) for x in fi]
        except Exception as exc:
            info["feature_importances_error"] = f"{type(exc).__name__}: {exc}"

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
