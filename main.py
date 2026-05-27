"""
PricePilot REST API for Copilot Studio / Power Automate.

Keboola Python/JS Data App entrypoint. Listens on port 5000.
GET /?api=true&base_fee=8500&tat=7&building_area=62000&number_of_stories=3&facility_type=Office
"""

from __future__ import annotations

import io
import math
import os
import warnings
from typing import Any

import joblib
import pandas as pd
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

INPUT_COLUMNS = [
    "base_fee", "tat", "portfolio_size", "building_area", "land_area",
    "facility_type", "secondary_property_type", "limit_of_liability",
    "travel_difficulty", "prior_report", "site_complexity", "country_code",
    "number_of_stories", "number_of_buildings", "total_units", "percent_units_to_inspect",
]
NUMERIC_COLUMNS = ["base_fee", "tat", "building_area", "number_of_stories"]
CATEGORICAL_COLUMNS = [c for c in INPUT_COLUMNS if c not in NUMERIC_COLUMNS]

_MODEL = None


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


def parse_input_row(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "pricing_id": int(params.get("pricing_id", 1)),
        "base_fee": float(params.get("base_fee", 5000)),
        "tat": int(params.get("tat", 5)),
        "portfolio_size": str(params.get("portfolio_size", "") or ""),
        "building_area": float(params.get("building_area", 40000)),
        "land_area": str(params.get("land_area", "") or ""),
        "facility_type": str(params.get("facility_type", "Industrial") or ""),
        "secondary_property_type": str(params.get("secondary_property_type", "") or ""),
        "limit_of_liability": str(params.get("limit_of_liability", "") or ""),
        "travel_difficulty": str(params.get("travel_difficulty", "") or ""),
        "prior_report": str(params.get("prior_report", "") or ""),
        "site_complexity": str(params.get("site_complexity", "") or ""),
        "country_code": str(params.get("country_code", "US") or ""),
        "number_of_stories": int(params.get("number_of_stories", 2)),
        "number_of_buildings": str(params.get("number_of_buildings", "") or ""),
        "total_units": str(params.get("total_units", "") or ""),
        "percent_units_to_inspect": str(params.get("percent_units_to_inspect", "") or ""),
    }


def build_pricing_results(input_row: dict[str, Any], predicted_total: float, is_rfp: bool) -> dict[str, Any]:
    return {
        "pricing_id": int(input_row["pricing_id"]),
        "base_fee": float(input_row["base_fee"]),
        "tat_fee": None,
        "portfolio_fee": None,
        "size_fee": None,
        "units_fee": None,
        "buildings_fee": None,
        "stories_fee": None,
        "travel_difficulty_fee": None,
        "prior_report_fee": None,
        "site_complexity_fee": None,
        "international_fee": None,
        "limit_of_liability_fee": None,
        "total_fee": int(math.ceil(float(predicted_total) / 50.0) * 50),
        "is_rfp": bool(is_rfp),
    }


def build_api_payload(input_row: dict[str, Any], predicted: float, is_rfp: bool) -> dict[str, Any]:
    results = build_pricing_results(input_row, predicted, is_rfp)
    base_fee = float(input_row["base_fee"])
    total_fee = float(results["total_fee"])
    multiplier = round(total_fee / base_fee, 4) if base_fee else 1.0
    return {
        "inputs": input_row,
        "results": results,
        "predicted_fee": total_fee,
        "predicted_multiplier": multiplier,
    }


def predict_from_query_params() -> dict[str, Any]:
    model = load_model()
    if model is None:
        return {"error": "ML model not loaded. Run pricing_model_training_transformation first."}

    params = {k: request.args.get(k) for k in request.args if k != "api"}
    input_row = parse_input_row(params)
    is_rfp = str(request.args.get("is_rfp", "false")).lower() in ("true", "1", "yes")
    features = prep_features(pd.DataFrame([input_row]))[INPUT_COLUMNS]
    predicted = float(model.predict(features)[0])
    return build_api_payload(input_row, predicted, is_rfp)


@app.route("/", methods=["GET", "POST"])
def root():
    if not request.args:
        return jsonify({"status": "running"})

    if request.args.get("api", "").lower() != "true":
        return jsonify({
            "error": "Missing api=true. Example: /?api=true&base_fee=5000&tat=7&building_area=40000&number_of_stories=2&facility_type=Office",
        }), 400

    try:
        payload = predict_from_query_params()
        if "error" in payload:
            return jsonify(payload), 503
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
