# Pricing API and more

> [!IMPORTANT]
> **Before making any change, read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first.**
> It is the source of truth for how the whole system fits together — components,
> the Keboola data layer and transformations, deployment, and the reasoning
> behind each decision — and it has a "where to start for common changes" guide.
> Keep it up to date when the architecture changes.

ML pricing for Partner Engineering and Science — Keboola Data App + Copilot Studio integration.

**Keboola project:** `skills_metrics` (10556)
**Schema:** `pricing_inputs` → `pricing_results`

## Repository layout

| Path | Purpose |
|------|---------|
| `api.py` | Flask REST API (deployed as the PricePilot **Python/JS** Data App) |
| `pricing_engine.py` | Rule-based engine (Python port of `Pricing.main_algo` from SL_Heaven) |
| `pricing_rules_calculator/` | Streamlit UI that calls the API and shows rule-based + ML side-by-side |
| `machine_learning/` | Training-data generator, model trainer, and Keboola transformation script |
| `connectors/` | Power Platform OpenAPI spec + Copilot Studio import guide |
| `keboola-config/` | Nginx + Supervisord for the Keboola Python data app runtime |
| `docs/copilot-instructions.md` | Copilot Studio system prompt |
| `pyproject.toml` | Python dependencies (Flask API) |
| `requirements.txt` | Python dependencies (Streamlit app Git deploy) |

## Deploy to Keboola (PricePilot API)

1. Open your [PricePilot API](https://connection.keboola.com/admin/projects/10556/data-apps) Data App (Python/JS).
2. **Deployment → Git**
   - Repository: `https://github.com/SamanollahiPartneresi/Pricing-API-and-more.git`
   - Branch: `main`
   - Use **repository root** (this folder has `api.py` at the top level).
3. **Do not** paste Python into the Git URL field.
4. Deploy and wait until status is **Running**.

## Deploy to Keboola (Service Pricing Tool — Streamlit)

The Streamlit app also deploys from this repo (no more copy-paste).

1. Open the **Service Pricing Tool** Data App → **Code Source → Git Repository**.
2. Repository: `https://github.com/SamanollahiPartneresi/Pricing-API-and-more`,
   branch `main`, **entrypoint** `pricing_rules_calculator/main.py`.
3. Private-repo auth: GitHub username + a fine-grained PAT with **Contents:
   Read-only** (packages come from the repo-root `requirements.txt`).
4. Deploy and wait until status is **Running**.

After that, ship UI changes by pushing to `main` and redeploying. See
[`docs/ARCHITECTURE.md` §11.1](docs/ARCHITECTURE.md).

## Test the API

```bash
curl -sS -X POST https://YOUR-APP-URL.hub.keboola.com/quote \
  -H 'Content-Type: application/json' \
  -d '{"order_form_service_id":4,"base_fee":2400,"tat":15,"primary_property_type":"Office","building_area":80000,"number_of_stories":3,"number_of_buildings":1,"country_code":"US"}'
```

Returns JSON with both `rule_based.total_fee` and `ml.predicted_fee`.

## Request flow (what happens per quote)

1. **Normalize input** in `parse_input_row` (`primary_property_type` and `facility_type` are treated as aliases).
2. **Run rule-based pricing** via `pricing_engine.calculate()` using live `pricing_factors` from Keboola.
3. **Run ML pricing** via the real-data LightGBM fee model bundle tagged `pricepilot_fee_model`.
4. **Assemble response** with `rule_based`, `ml`, `comparison`, and `ml_error` (if ML is unavailable).

## Train / retrain the ML model

The production ML path uses the real-data fee model (`train_fee_model.py`) and
loads the `pricepilot_fee_model` bundle in `api.py`. See `machine_learning/README.md`
for current training, metrics, and artifact sync details.

To iterate locally against a CSV export of the training table:

```bash
python -m venv .venv && source .venv/bin/activate
pip install lightgbm pandas numpy scikit-learn joblib
FEE_INPUT_PATH=/path/to/pricing_fee_model_input.csv python machine_learning/train_fee_model.py
```

## Copilot Studio

The agent uses a Power Platform Custom Connector (see `connectors/`) backed by the Flask API.

## Local run (Flask API)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
export KBC_TOKEN=... KBC_URL=https://connection.keboola.com
python api.py
```

Useful health/debug endpoints:

- `GET /health` for liveness
- `GET /ready` for readiness checks (factors + fee-model availability)
- `GET /services` for service list/default fees
