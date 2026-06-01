# Pricing API and more

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
| `pyproject.toml` | Python dependencies |

## Deploy to Keboola (PricePilot API)

1. Open your [PricePilot API](https://connection.keboola.com/admin/projects/10556/data-apps) Data App (Python/JS).
2. **Deployment → Git**
   - Repository: `https://github.com/SamanollahiPartneresi/Pricing-API-and-more.git`
   - Branch: `main`
   - Use **repository root** (this folder has `api.py` at the top level).
3. **Do not** paste Python into the Git URL field.
4. Deploy and wait until status is **Running**.

## Test the API

```bash
curl -sS -X POST https://YOUR-APP-URL.hub.keboola.com/quote \
  -H 'Content-Type: application/json' \
  -d '{"order_form_service_id":4,"base_fee":2400,"tat":15,"primary_property_type":"Office","building_area":80000,"number_of_stories":3,"number_of_buildings":1,"country_code":"US"}'
```

Returns JSON with both `rule_based.total_fee` and `ml.predicted_fee`.

## Train / retrain the ML model

The model is rebuilt by a Keboola Python transformation that reads `pricing_factors`,
generates synthetic training data from the canonical rule engine, and saves a
`pricepilot_model.pkl` tagged `pricepilot_model`. See `machine_learning/README.md`.

To iterate locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install pandas numpy scikit-learn joblib
python machine_learning/generate_training_data.py   # writes training_data.csv
python machine_learning/train_model.py              # writes pricepilot_model.pkl + training_metrics.json
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
