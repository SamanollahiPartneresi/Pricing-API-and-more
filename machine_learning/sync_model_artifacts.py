"""
Sync PricePilot fee-model artifacts from Keboola Storage into the repo.

Pulls the two small output tables written by the `pricepilot_fee_model_training`
transformation and regenerates the committed snapshots so GitHub always reflects
the deployed model:

  out.c-pricing_ml.fee_model_importance  -> fee_model_importance.csv + FEATURE_IMPORTANCE.md
  out.c-pricing_ml.fee_model_metrics     -> fee_model_metrics.csv    + MODEL_METRICS.md
  out.c-pricing_ml.fee_model_runs        -> fee_model_runs.csv       + MODEL_LOG.md

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
# Use `or` (not get's default) so an empty KBC_URL env var — which CI sets when the
# optional KBC_URL secret is absent — still falls back to the default stack.
KBC_URL = (os.environ.get("KBC_URL") or "https://connection.keboola.com").rstrip("/")
IMPORTANCE_TABLE = os.environ.get(
    "IMPORTANCE_TABLE_ID", "out.c-pricing_ml.fee_model_importance"
)
METRICS_TABLE = os.environ.get(
    "METRICS_TABLE_ID", "out.c-pricing_ml.fee_model_metrics"
)
RUNS_TABLE = os.environ.get(
    "RUNS_TABLE_ID", "out.c-pricing_ml.fee_model_runs"
)


def fetch_table(
    table_id: str, token: str, *, required: bool = True
) -> list[dict[str, str]]:
    """Return rows of a Storage table via the data-preview endpoint (CSV).
    These artifact tables are tiny (tens of rows), so a preview is sufficient.

    When ``required`` is False, a missing/empty table is treated as a soft skip
    (returns []) instead of aborting — used for the append-only run log, which
    may not exist on older deployments."""
    url = f"{KBC_URL}/v2/storage/tables/{table_id}/data-preview"
    resp = requests.get(
        url,
        headers={"X-StorageApi-Token": token},
        params={"limit": 1000, "format": "rfc"},
        timeout=60,
    )
    if resp.status_code != 200:
        body = resp.text[:500].replace("\n", " ")
        if not required:
            print(f"WARN: skipping {table_id}: HTTP {resp.status_code} — {body}")
            return []
        # Surface the real cause in the CI log instead of a bare traceback.
        raise SystemExit(
            f"ERROR fetching {table_id}: HTTP {resp.status_code} from {url}\n"
            f"Response: {body}\n"
            "Check that the KBC_TOKEN secret is set, valid, and has READ access to "
            f"the bucket containing {table_id} (out.c-pricing_ml)."
        )
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = [dict(row) for row in reader]
    if not rows:
        if not required:
            print(f"WARN: {table_id} returned 0 rows — skipping.")
            return []
        raise SystemExit(f"ERROR: {table_id} returned 0 rows. Has the training run produced it yet?")
    return rows


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


# Columns of the run log rendered into MODEL_LOG.md, in display order.
# (label, run-log key, formatter)
RUN_COLUMNS: list[tuple[str, str, str]] = [
    ("Run (UTC)", "run_at", "ts"),
    ("Tag", "model_tag", "tag"),
    ("# feats", "n_features", "int"),
    ("Rows (train)", "rows_total", "int"),
    ("Rows (test)", "rows_test", "int"),
    ("MAE", "test_mae", "usd"),
    ("RMSE", "test_rmse", "usd"),
    ("Median err", "test_median_ape_pct", "pct"),
    ("Mean err", "test_mape_pct", "pct"),
    ("Within 10%", "test_within_10pct", "pct"),
    ("Within 20%", "test_within_20pct", "pct"),
    ("Test R²", "test_r2", "r2"),
    ("Train R²", "train_r2", "r2"),
]


def _fmt_run_cell(kind: str, raw: str) -> str:
    if raw is None or raw == "":
        return "—"
    if kind == "ts":
        # 2026-06-02T22:47:15+00:00 -> 2026-06-02 22:47
        return raw.replace("T", " ")[:16]
    if kind == "tag":
        # Strip the common 'pricepilot_fee_model' prefix for a compact label.
        if raw == "pricepilot_fee_model":
            return "prod"
        return raw.replace("pricepilot_fee_model", "").lstrip("_") or raw
    if kind == "int":
        return f"{_f(raw):,.0f}"
    if kind == "usd":
        return f"${_f(raw):,.0f}"
    if kind == "pct":
        return f"{_f(raw):.1f}%"
    if kind == "r2":
        return f"{_f(raw):.3f}"
    return raw


def write_runs(rows: list[dict[str, str]]) -> None:
    """Render the append-only training run log (newest first) into MODEL_LOG.md
    plus a flat fee_model_runs.csv snapshot. No-op if the table is empty."""
    if not rows:
        return
    ordered = sorted(rows, key=lambda r: r.get("run_at", ""), reverse=True)

    # Flat CSV snapshot (stable column order, all columns from the source table).
    csv_cols = [
        "run_id", "run_at", "model_tag", "n_features", "rows_total", "rows_test",
        "test_mae", "test_rmse", "test_median_ape_pct", "test_mape_pct",
        "test_within_10pct", "test_within_20pct", "test_r2", "train_r2", "features",
    ]
    csv_path = HERE / "fee_model_runs.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(csv_cols)
        for r in ordered:
            writer.writerow([r.get(c, "") for c in csv_cols])

    today = dt.date.today().isoformat()
    header = "| " + " | ".join(label for label, _, _ in RUN_COLUMNS) + " |"
    sep = "|" + "|".join(["---:" if i else "---" for i in range(len(RUN_COLUMNS))]) + "|"
    lines = [
        "# PricePilot fee model — run log",
        "",
        "Append-only history of every `pricepilot_fee_model_training` run, newest first.",
        "Use it to compare model versions over time (accuracy, feature count, data size).",
        "",
        "- Source of truth (one row appended per retrain): Keboola table "
        f"`{RUNS_TABLE}` (primary key `run_id`).",
        "- Raw values: [`fee_model_runs.csv`](./fee_model_runs.csv).",
        "- Metrics are on the 20% hold-out test split; lower error / higher "
        '"within X%" and R² is better.',
        "",
        f"_Auto-synced from Keboola on {today} by `sync_model_artifacts.py`._",
        "",
        header,
        sep,
    ]
    for r in ordered:
        cells = [_fmt_run_cell(kind, r.get(key, "")) for _, key, kind in RUN_COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # Per-run feature lists (so a reader can see exactly what changed).
    lines.append("## Feature set per run")
    lines.append("")
    for r in ordered:
        ts = _fmt_run_cell("ts", r.get("run_at", ""))
        tag = _fmt_run_cell("tag", r.get("model_tag", ""))
        feats = (r.get("features") or "").strip()
        feat_md = ", ".join(f"`{f}`" for f in feats.split(",") if f) if feats else "—"
        lines.append(
            f"- **{ts}** · _{tag}_ ({_fmt_run_cell('int', r.get('n_features',''))} features): {feat_md}"
        )
    lines.append("")
    (HERE / "MODEL_LOG.md").write_text("\n".join(lines))


def main() -> int:
    token = os.environ.get("KBC_TOKEN")
    if not token:
        print(
            "ERROR: KBC_TOKEN is empty. Add it under GitHub -> Settings -> "
            "Secrets and variables -> Actions -> New repository secret (name: KBC_TOKEN).",
            file=sys.stderr,
        )
        return 1
    print(
        f"Stack: {KBC_URL} | token: ...{token[-4:]} | tables: "
        f"{IMPORTANCE_TABLE}, {METRICS_TABLE}, {RUNS_TABLE}"
    )
    write_importance(fetch_table(IMPORTANCE_TABLE, token))
    write_metrics(fetch_table(METRICS_TABLE, token))
    write_runs(fetch_table(RUNS_TABLE, token, required=False))
    print("Synced fee_model_importance + fee_model_metrics + fee_model_runs artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
