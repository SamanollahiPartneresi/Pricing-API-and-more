"""
PricePilot REST API — rule-based + ML pricing for Copilot Studio / Power Automate.

Keboola Python/JS Data App entrypoint. Listens on port 5000.

GET /?api=true&order_form_service_id=4&base_fee=2400&tat=7&building_area=62000
        &number_of_stories=3&facility_type=Office&country_code=US

POST /quote  (JSON body with the same fields)

Both endpoints run BOTH engines on each request:
    * rule-based: SL_Heaven `Pricing.main_algo` port (see `pricing_engine.py`),
      evaluated against the live `pricing_factors` table in Keboola Storage.
    * ML: scikit-learn model trained by `pricing_model_training_transformation`
      and loaded from a Keboola Storage file tagged `pricepilot_model`.
"""

from __future__ import annotations

import io
import math
import os
import time
import warnings
from typing import Any

import joblib
import pandas as pd
import requests
from flask import Flask, jsonify, request

import pricing_engine
from pricing_engine import (
    FEE_COLUMNS,
    SERVICE_BASE_FEES,
    SERVICE_NAMES,
    breakdown_rows,
    calculate as rule_based_calculate,
)

app = Flask(__name__)

INPUT_COLUMNS = [
    "base_fee", "tat", "portfolio_size", "building_area", "land_area",
    "facility_type", "secondary_property_type", "limit_of_liability",
    "travel_difficulty", "prior_report", "site_complexity", "country_code",
    "number_of_stories", "number_of_buildings", "total_units", "percent_units_to_inspect",
]
NUMERIC_COLUMNS = ["base_fee", "tat", "building_area", "number_of_stories"]
CATEGORICAL_COLUMNS = [c for c in INPUT_COLUMNS if c not in NUMERIC_COLUMNS]

PRICING_FACTORS_TABLE_ID = os.environ.get(
    "PRICING_FACTORS_TABLE_ID", "in.c-Pricing_Agent_Input_Data.pricing_factors"
)
PRICING_FACTORS_TTL_SECONDS = int(os.environ.get("PRICING_FACTORS_TTL_SECONDS", "600"))
DEFAULT_ORDER_FORM_SERVICE_ID = int(os.environ.get("DEFAULT_ORDER_FORM_SERVICE_ID", "4"))

_MODEL = None
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


# Pricing factors loader (Storage data-preview API, 10-minute cache)


PRICING_FACTORS_LOCAL_CSV = "/data/in/tables/pricing_factors.csv"


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


def _export_table_to_dataframe(
    table_id: str, token: str, base_url: str, *, max_wait_s: int = 60
) -> pd.DataFrame:
    """Async export of a Keboola Storage table to a CSV file, then download.
    Works for tables of any size (data-preview is capped at 100 rows).
    """
    headers = {"X-StorageApi-Token": token}

    create = requests.post(
        f"{base_url}/v2/storage/tables/{table_id}/export-async",
        headers=headers,
        json={"format": "rfc", "gzip": False},
        timeout=30,
    )
    create.raise_for_status()
    job = create.json()
    job_url = job.get("url") or f"{base_url}/v2/storage/jobs/{job['id']}"

    start = time.time()
    while True:
        poll = requests.get(job_url, headers=headers, timeout=30)
        poll.raise_for_status()
        job = poll.json()
        status = job.get("status")
        if status == "success":
            break
        if status == "error":
            raise RuntimeError(f"Storage export job failed: {job.get('error')}")
        if time.time() - start > max_wait_s:
            raise TimeoutError(
                f"Storage export job did not finish within {max_wait_s}s (status={status})."
            )
        time.sleep(0.5)

    file_id = ((job.get("results") or {}).get("file") or {}).get("id")
    if not file_id:
        raise RuntimeError(f"Storage export job finished but returned no file id: {job}")

    file_meta = requests.get(
        f"{base_url}/v2/storage/files/{file_id}",
        headers=headers,
        params={"federationToken": 1},
        timeout=30,
    )
    file_meta.raise_for_status()
    download_url = file_meta.json().get("url")
    if not download_url:
        raise RuntimeError("Storage file meta has no download URL.")

    csv_bytes = requests.get(download_url, timeout=60).content
    return pd.read_csv(io.BytesIO(csv_bytes))


def load_pricing_factors(force_refresh: bool = False) -> pd.DataFrame:
    """Load the `pricing_factors` table.

    Tries in order:
      1. Local CSV at `/data/in/tables/pricing_factors.csv` (set when the
         data app has an input mapping configured — cheapest, no API calls).
      2. Keboola Storage async export — works for any table size.

    Cached in-process for PRICING_FACTORS_TTL_SECONDS.
    """
    global _FACTORS, _FACTORS_LOADED_AT
    now = time.time()
    if (
        not force_refresh
        and _FACTORS is not None
        and (now - _FACTORS_LOADED_AT) < PRICING_FACTORS_TTL_SECONDS
    ):
        return _FACTORS

    if os.path.exists(PRICING_FACTORS_LOCAL_CSV):
        df = pd.read_csv(PRICING_FACTORS_LOCAL_CSV)
    else:
        token = os.environ.get("KBC_TOKEN")
        base_url = os.environ.get("KBC_URL", "https://connection.keboola.com").rstrip("/")
        if not token:
            raise RuntimeError(
                "KBC_TOKEN env var is not set and no local pricing_factors.csv was "
                "mounted; cannot load pricing factors."
            )
        df = _export_table_to_dataframe(PRICING_FACTORS_TABLE_ID, token, base_url)

    df = _normalize_factors_df(df)
    _FACTORS = df
    _FACTORS_LOADED_AT = now
    return df


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
        "facility_type": _to_str(params.get("facility_type"), "Industrial"),
        "secondary_property_type": _to_str(params.get("secondary_property_type")),
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
    factors_all = load_pricing_factors()
    service_id = int(input_row["order_form_service_id"])
    factors_df = factors_all[
        factors_all["order_form_service_id"] == service_id
    ].copy()

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


def run_ml(input_row: dict[str, Any], is_rfp: bool) -> dict[str, Any]:
    """Run the joblib model on the input row. Returns a structured dict or
    raises with a descriptive error suitable for surfacing to the caller."""
    model = load_model()
    if model is None:
        raise RuntimeError(
            "ML model not loaded. Run pricing_model_training_transformation and "
            "tag the joblib file with `pricepilot_model`."
        )

    feature_row = {
        "base_fee": float(input_row["base_fee"]),
        "tat": int(input_row["tat"]),
        "portfolio_size": str(input_row["portfolio_size"]),
        "building_area": float(input_row["building_area"]),
        "land_area": str(input_row["land_area"]),
        "facility_type": str(input_row["facility_type"]),
        "secondary_property_type": str(input_row["secondary_property_type"]),
        "limit_of_liability": str(input_row["limit_of_liability"]),
        "travel_difficulty": str(input_row["travel_difficulty"]),
        "prior_report": str(input_row["prior_report"]),
        "site_complexity": str(input_row["site_complexity"]),
        "country_code": str(input_row["country_code"]),
        "number_of_stories": int(input_row["number_of_stories"]),
        "number_of_buildings": str(input_row["number_of_buildings"]),
        "total_units": str(input_row["total_units"]),
        "percent_units_to_inspect": str(input_row["percent_units_to_inspect"]),
    }
    features = prep_features(pd.DataFrame([feature_row]))[INPUT_COLUMNS]
    predicted = float(model.predict(features)[0])
    base_fee = float(input_row["base_fee"]) or 1.0
    return {
        "predicted_fee": int(math.ceil(predicted / 50.0) * 50),
        "predicted_fee_raw": round(predicted, 2),
        "predicted_multiplier": round(predicted / base_fee, 4),
        "is_rfp": bool(is_rfp),
    }


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
            "error": "Missing api=true. Example: /?api=true&order_form_service_id=4&base_fee=2400&tat=7&facility_type=Office",
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


@app.route("/pricing-factors", methods=["GET"])
def pricing_factors_endpoint():
    """Return the raw factor rules for a service (debug/explain support)."""
    try:
        service_id = int(request.args.get("service_id", DEFAULT_ORDER_FORM_SERVICE_ID))
        factors = load_pricing_factors()
        if "order_form_service_id" not in factors.columns:
            return jsonify({
                "error": "pricing_factors table is missing 'order_form_service_id' column",
                "columns_seen": list(factors.columns),
                "row_count": int(len(factors)),
                "first_row": (factors.head(1).to_dict(orient="records") or [None])[0],
            }), 500
        wanted_cols = [c for c in ("category", "level", "description", "value") if c in factors.columns]
        rows = (
            factors[factors["order_form_service_id"] == service_id]
            [wanted_cols]
            .astype(str)
            .to_dict(orient="records")
        )
        return jsonify({
            "order_form_service_id": service_id,
            "service_name": SERVICE_NAMES.get(service_id),
            "rows": rows,
            "row_count": len(rows),
        })
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
