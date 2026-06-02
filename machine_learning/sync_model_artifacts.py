"""
Sync PricePilot fee-model artifacts from Keboola Storage into the repo.

Pulls the two small output tables written by the `pricepilot_fee_model_training`
transformation and regenerates the committed snapshots so GitHub always reflects
the deployed model:

  out.c-pricing_ml.fee_model_importance  -> fee_model_importance.csv + FEATURE_IMPORTANCE.md
  out.c-pricing_ml.fee_model_metrics     -> fee_model_metrics.csv    + MODEL_METRICS.md

Run locally:  KBC_TOKEN=... python machine_learning/sync_model_artifacts.py
In CI:        invoked by .github/workflows/sync-model-artifacts.yml (token from secrets).

Only the standard library + `requests` are required.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
KBC_URL = os.environ.get("KBC_URL", "https://connection.keboola.com").rstrip("/")
IMPORTANCE_TABLE = os.environ.get(
    "IMPORTANCE_TABLE_ID", "out.c-pricing_ml.fee_model_importance"
)
METRICS_TABLE = os.environ.get(
    "METRICS_TABLE_ID", "out.c-pricing_ml.fee_model_metrics"
)


def fetch_table(table_id: str, token: str) -> list[dict[str, str]]:
    """Return rows of a Storage table via the data-preview endpoint (CSV).
    These artifact tables are tiny (tens of rows), so a preview is sufficient."""
    resp = requests.get(
        f"{KBC_URL}/v2/storage/tables/{table_id}/data-preview",
        headers={"X-StorageApi-Token": token},
        params={"limit": 10000},
        timeout=60,
    )
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return [dict(row) for row in reader]


def _f(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def write_importance(rows: list[dict[str, str]]) -> None:
    ranked = sorted(rows, key=lambda r: _f(r.get("gain")), reverse=True)
    total = sum(_f(r.get("gain")) for r in ranked) or 1.0

    csv_path = HERE / "fee_model_importance.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "feature", "gain", "share_pct"])
        for i, r in enumerate(ranked, start=1):
            gain = _f(r.get("gain"))
            writer.writerow([i, r.get("feature", ""), f"{gain:.2f}", f"{gain / total * 100:.1f}"])

    today = dt.date.today().isoformat()
    lines = [
        "# PricePilot fee model — feature importance",
        "",
        "Gain-based feature importance for the LightGBM fee model (`pricepilot_fee_model`),",
        "trained by the `pricepilot_fee_model_training` Keboola transformation on the",
        "historical-quote table `pricing_fee_model_input`.",
        "",
        "- **Gain** = total reduction in training loss attributed to splits on that feature",
        "  (higher = more influential). **Share** = each feature's gain as a % of the total.",
        f"- Source of truth (regenerated on every retrain): Keboola table `{IMPORTANCE_TABLE}`.",
        "- Raw values: [`fee_model_importance.csv`](./fee_model_importance.csv).",
        "",
        f"_Auto-synced from Keboola on {today} by `sync_model_artifacts.py`._",
        "",
        "| Rank | Feature | Gain | Share |",
        "|---:|---|---:|---:|",
    ]
    for i, r in enumerate(ranked, start=1):
        gain = _f(r.get("gain"))
        lines.append(f"| {i} | `{r.get('feature','')}` | {gain:,.0f} | {gain / total * 100:.1f}% |")
    lines.append("")
    (HERE / "FEATURE_IMPORTANCE.md").write_text("\n".join(lines))


# Friendlier labels + units for the metric keys emitted by the trainer.
METRIC_LABELS = {
    "n": ("Rows", ""),
    "mae": ("MAE", "$"),
    "rmse": ("RMSE", "$"),
    "median_ape_pct": ("Median error", "%"),
    "mape_pct": ("Mean error (MAPE)", "%"),
    "within_10pct": ("Within 10%", "%"),
    "within_20pct": ("Within 20%", "%"),
    "r2_dollars": ("R² (dollars)", ""),
}
METRIC_ORDER = ["n", "mae", "rmse", "median_ape_pct", "mape_pct", "within_10pct", "within_20pct", "r2_dollars"]


def _fmt_metric(key: str, value: float) -> str:
    _, unit = METRIC_LABELS.get(key, (key, ""))
    if key == "n":
        return f"{value:,.0f}"
    if unit == "$":
        return f"${value:,.0f}"
    if unit == "%":
        return f"{value:.1f}%"
    return f"{value:.3f}"


def write_metrics(rows: list[dict[str, str]]) -> None:
    csv_path = HERE / "fee_model_metrics.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["scope", "metric", "value"])
        for r in sorted(rows, key=lambda r: (r.get("scope", ""), r.get("metric", ""))):
            writer.writerow([r.get("scope", ""), r.get("metric", ""), r.get("value", "")])

    # Pivot to scope -> {metric: value}
    by_scope: dict[str, dict[str, float]] = {}
    for r in rows:
        by_scope.setdefault(r.get("scope", ""), {})[r.get("metric", "")] = _f(r.get("value"))

    def scope_block(title: str, scopes: list[str]) -> list[str]:
        present = [s for s in scopes if s in by_scope]
        if not present:
            return []
        header = "| Metric | " + " | ".join(present) + " |"
        sep = "|---|" + "|".join(["---:"] * len(present)) + "|"
        out = [f"### {title}", "", header, sep]
        for key in METRIC_ORDER:
            label = METRIC_LABELS.get(key, (key, ""))[0]
            cells = [_fmt_metric(key, by_scope[s][key]) for s in present if key in by_scope[s]]
            if len(cells) != len(present):
                continue
            out.append(f"| {label} | " + " | ".join(cells) + " |")
        out.append("")
        return out

    today = dt.date.today().isoformat()
    service_scopes = sorted(s for s in by_scope if s.startswith("service_test::"))
    lines = [
        "# PricePilot fee model — accuracy metrics",
        "",
        "Hold-out (20% test split) accuracy for the deployed LightGBM fee model, vs. two",
        "naive baselines. Lower error / higher \"within X%\" and R² is better.",
        "",
        f"- Source of truth (regenerated on every retrain): Keboola table `{METRICS_TABLE}`.",
        "- Raw values: [`fee_model_metrics.csv`](./fee_model_metrics.csv).",
        "",
        f"_Auto-synced from Keboola on {today} by `sync_model_artifacts.py`._",
        "",
    ]
    lines += scope_block("Model vs. baselines (test set)",
                         ["model_test", "baseline_predict_base_fee", "baseline_predict_suggested_fee"])
    lines += scope_block("Train vs. test (overfit check)", ["model_train", "model_test"])
    if service_scopes:
        lines += scope_block("Per-service (test set)", service_scopes)
    (HERE / "MODEL_METRICS.md").write_text("\n".join(lines))


def main() -> int:
    token = os.environ.get("KBC_TOKEN")
    if not token:
        print("ERROR: KBC_TOKEN not set.", file=sys.stderr)
        return 1
    write_importance(fetch_table(IMPORTANCE_TABLE, token))
    write_metrics(fetch_table(METRICS_TABLE, token))
    print("Synced fee_model_importance + fee_model_metrics artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
