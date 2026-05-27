# Pricing API and more

ML pricing for Partner Engineering and Science — Keboola Data App + Copilot Studio integration.

**Keboola project:** `skills_metrics` (10556)  
**Schema:** `pricing_inputs` → `pricing_results`

## Repository layout

| Path | Purpose |
|------|---------|
| `main.py` | Flask REST API (deploy as **Python/JS** Data App) |
| `pyproject.toml` | Python dependencies |
| `keboola-config/` | Nginx + Supervisord for Keboola runtime |
| `docs/copilot-instructions.md` | Copilot Studio system prompt |

## Deploy to Keboola (PricePilot API)

1. Open your [PricePilot API](https://connection.keboola.com/admin/projects/10556/data-apps) Data App (Python/JS).
2. **Deployment → Git**
   - Repository: `https://github.com/SamanollahiPartneresi/Pricing-API-and-more.git`
   - Branch: `main`
   - Use **repository root** (this folder has `main.py` at the top level).
3. **Do not** paste Python into the Git URL field.
4. Deploy and wait until status is **Running**.
5. Copy basic-auth credentials from **Open Data App**.

## Test the API

```bash
curl -sS -u "USER:PASS" \
  "https://YOUR-APP-URL.hub.keboola.com/?api=true&base_fee=8500&tat=7&building_area=62000&number_of_stories=3&facility_type=Office"
```

Expected: JSON with `results.total_fee` and `predicted_fee`.

## Copilot Studio

Point your HTTP action at the **Flask API URL** (not the Streamlit app). Use **Basic Auth**. See `docs/copilot-instructions.md`.

## Prerequisites in Keboola

- Trained model file tagged `pricepilot_model` (from `pricing_model_training_transformation`)
- Storage tables: `pricing_factors`, `pricing_inputs`, `pricing_results`

## Local run (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
export KBC_TOKEN=... KBC_URL=https://connection.keboola.com
python main.py
```

Then open: `http://localhost:5000/?api=true&base_fee=5000&tat=5&building_area=40000&number_of_stories=2&facility_type=Industrial`
