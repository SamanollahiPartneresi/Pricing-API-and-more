# PricePilot — Technical Report

*Partner Engineering and Science · Internal pricing automation*

---

## 1. Executive summary

PricePilot is an AI pricing assistant that produces project-fee estimates on demand inside Microsoft Teams. For each request it runs **two pricing methods on the same inputs** and returns both:

1. A **rule-based engine** — a faithful Python port of the canonical production pricing algorithm (`Pricing.main_algo` from the SL_Heaven Rails app).
2. A **machine-learning model** — a gradient-boosted regressor that offers a data-driven comparison.

The system spans a data layer (Keboola), a compute/API layer (Flask), an integration layer (Power Platform custom connector), and two front ends (a Copilot Studio agent in Teams and a Streamlit web app). It quotes four services: **PCA Equity, ESA, Zoning, and PCA Debt**.

---

## 2. Architecture

```
   User (Teams)                     Browser
        │                              │
   Copilot Studio agent          Streamlit app
        │  (natural language)          │
   Power Platform                      │
   Custom Connector (OpenAPI 2.0)      │
        │                              │
        └────────────► Flask API (Keboola data app) ◄────┘
                          │        │
              rule-based engine   ML model (.pkl)
                          │
              Keboola pricing_factors table
```

| Layer | Component | Technology | Notes |
|-------|-----------|------------|-------|
| Interface | Copilot Studio agent | Microsoft Copilot Studio + Teams | Conversational quoting |
| Interface | Pricing calculator | Streamlit (Keboola data app) | Browser-based, same engine |
| Integration | Custom Connector | Power Platform / OpenAPI 2.0 | Exposes 3 API actions to the agent |
| Compute | Pricing API | Python 3 + Flask | Combines both methods into one response |
| Logic | Rule engine | `pricing_engine.py` (pandas) | Port of `Pricing.main_algo` |
| Logic | ML model | LightGBM fee model | Loaded from the `pricepilot_fee_model` bundle |
| Data | Pricing factors | Keboola Storage table | Production coefficients (~257 rows) |
| Hosting | Keboola Data Apps | python-js (API) + streamlit (UI) | Git-backed, auto-suspend |

---

## 3. Data layer

The rule-based engine is driven entirely by a Keboola Storage table, `in.c-Pricing_Agent_Input_Data.pricing_factors`, with columns:

`order_form_service_id`, `category`, `level`, `description`, `value`.

Each service (1 = PCA Equity, 2 = ESA, 3 = Zoning, 4 = PCA Debt) has its own set of factor rows across ~12 categories (turnaround time, limit of liability, portfolio size, travel difficulty, site complexity, prior report, building size, etc.). Updating pricing is a **data change** in this table — no code deploy required.

Default base fees (used when the caller supplies none): **PCA Equity $4,000, ESA $2,200, Zoning $2,500, PCA Debt $2,400.**

---

## 4. Rule-based pricing engine (`pricing_engine.py`)

A line-for-line port of the canonical Ruby algorithm, operating on the `pricing_factors` DataFrame. Key behaviors:

- **12-category fee model** — base fee plus per-category surcharges (limit of liability, turnaround, portfolio, travel, site complexity, prior report, size, etc.), summed and rounded to the nearest $50.
- **Facility-type awareness** — size is driven by *building area (SF)* for most types, but by *total units + % of units to inspect* for Multi-Family and Seniors Housing.
- **Size-level precedence fix** — corrects a Ruby alphabetical-sort bug (`XS < S < M < L < XL < 2XL < 3XL`).
- **RFP fallback** — turnaround outside the supported window returns `is_rfp = true` rather than a fabricated number.
- **Per-TAT scale** — can return the price at every turnaround day for "what-if" analysis.

The engine is shared by both the API and the Streamlit app, guaranteeing identical results across surfaces.

---

## 5. Machine-learning model

- **Algorithm:** LightGBM gradient-boosted trees with native categorical + NaN handling. Target: `log_fee` (natural log of the awarded scope fee); predictions are exponentiated back to dollars.
- **Training data:** ~141k cleaned/winsorized **real historical quotes** (`pricing_fee_model_input`) across Phase I ESA, Equity PCA, and Debt PCA. Cost/margin, final fee, status, and raw identifiers are excluded as features to prevent leakage.
- **Training pipeline** (`machine_learning/`): `train_fee_model.py` trains and evaluates (MAE / MAPE / R²) and writes the `pricepilot_fee_model` bundle; `keboola_fee_training_script.py` is a self-contained version for a Keboola Python transformation; `sync_model_artifacts.py` keeps the committed metrics/importance snapshots in sync with Keboola Storage.
- **Note:** the model gives a data-driven estimate plus a likely range. **The rule-based total remains the source of record**; the ML prediction is a comparison/reality-check alongside it.

---

## 6. API service (`api.py`, Flask)

A single Flask app combines both methods and returns one JSON document. Endpoints:

| Method / Path | Purpose |
|---------------|---------|
| `POST /quote` | Primary: rule-based + ML quote from a JSON body |
| `GET /?api=true&...` | Same quote via query string |
| `GET /services` | Services with default base fees and factor counts |
| `GET /pricing-factors?service_id=` | Factor rules for one service |
| `GET /health` | Lightweight liveness check (does not load the model) |
| `GET /` | Service/endpoint info |
| `GET /debug/ml-info`, `GET /debug/files` | Introspection (recommend restricting in prod) |

**Request parameters** (all optional except `order_form_service_id`): `order_form_service_id`, `tat`, `base_fee`, `primary_property_type` (alias `facility_type`), `building_area`, `total_units`, `percent_units_to_inspect`, `number_of_stories`, `number_of_buildings`, `country_code`, `travel_difficulty`, `site_complexity`, `prior_report`, `limit_of_liability`, `portfolio_size`, `land_area`, `secondary_property_type`.

**Response shape:**
```
{
  "rule_based": { "service_name", "base_fee", "total_fee", "is_rfp",
                  "breakdown": [ { "category", "amount", "level", ... } ] },
  "ml":         { "predicted_fee", "predicted_multiplier", ... },
  "comparison": { "rule_based_total", "ml_total", "delta_pct" },
  "ml_error":   null
}
```
The app degrades gracefully: if the model fails to load, `ml_error` is populated and the rule-based result is still returned.

---

## 7. Integration layer — Power Platform Custom Connector

The API is exposed to Copilot Studio through a custom connector defined by an **OpenAPI 2.0** spec (`connectors/pricepilot-api.openapi.yaml`). It publishes three actions the agent can call:

- **GetQuote** — "Get a combined rule-based and ML quote" (maps to `POST /quote`).
- **ListServices** — "List pricing services" (`GET /services`); also used as a silent warm-up call.
- **GetPricingFactors** — "Get pricing factor rules for one service" (`GET /pricing-factors`).

The connector uses **no authentication** (the API is open), so users need no credentials — a per-environment connection is created once.

---

## 8. Conversational agent (Copilot Studio in Teams)

The agent is configured via a ~6.5 KB instruction prompt (fits Copilot Studio's 8 KB limit) that governs:

- **Guided collection** — asks for required fields one at a time (service, primary property type, turnaround, size driver), defaults the rest.
- **Flexible input** — accepts free-form descriptions, a fill-in template, or starter-button prompts.
- **Response formatting** — presents rule-based total, ML comparison, base fee, delta %, and a plain-English factor breakdown; never leaks raw JSON, tool names, or internal fields.
- **RFP / error handling** — special-cases `is_rfp` and `ml_error`.

**Cold-start handling (key operational design):** the Keboola app sleeps after 15 minutes idle. Measured behavior: a request to a sleeping app returns an immediate 503 but *triggers* a wake that completes in ~10–15 s. The agent mitigates this two ways: (1) a **silent warm-up** call on the first pricing turn, which wakes the engine while the user is still answering questions; (2) **automatic retry** of a failed call up to 3 times, since the natural spacing between attempts covers the wake. A one-line "warming up" heads-up sets expectations on the first message.

---

## 9. Streamlit calculator

A browser front end (Keboola streamlit data app) that imports the same `pricing_engine.py`, letting users run quotes and inspect the full factor breakdown without Teams. Useful for ad-hoc analysis and for validating the rule engine independently of the agent.

---

## 10. Operations, cost, and security

- **Hosting:** both front ends and the API run as Keboola Data Apps on a **`tiny`** backend; the python-js API is backed by a managed Git repo (`Pricing-API-and-more`) and redeploys from `main`.
- **Cost:** ~0.1 credits/hour while running. With 15-minute auto-suspend the app only runs when used; keeping it warm during business hours would add ~26 credits/month, fully always-on ~72 credits/month.
- **Cold start:** ~10–15 s wake, handled by the agent (see §8); optional keep-warm (scheduled ping or always-on) eliminates it entirely.
- **Security:** the API is intentionally open (no-auth) so the connector and Streamlit app can call it; it returns **only pricing estimates — no PII or credentials**. Hardening options: restrict the `/debug/*` endpoints and add a shared-secret header if the surface needs locking down.

---

## 11. Roadmap & known limitations

- **Retrain the ML model on real historical quotes** (currently synthetic; rule-based remains source of record).
- **Adaptive Card form** in Teams for one-click structured entry.
- **Optional keep-warm** for guaranteed-instant first quotes.
- **Harden the API** (restrict debug endpoints; optional auth).
- Expand services/factors as production pricing rules evolve.

---
*Prepared for internal technical review — Partner Engineering and Science.*
