# PricePilot / Service Pricing Tool — Architecture

> **Read this first.** This is the single source of truth for how the whole
> pricing system is wired together: the components, the data, the deployment
> model, and — importantly — **why** each decision was made. If you're coming
> back to make a change, jump to [§13 "Where to start for common changes"](#13-where-to-start-for-common-changes).

Last updated: 2026-06-19.

---

## 1. What this system is

PricePilot produces commercial-property **project-fee estimates** on demand. For
every request it runs **two independent pricing methods on the same inputs** and
returns both:

1. **Rule-based engine** — a faithful Python port of the canonical production
   algorithm (`Pricing.main_algo` from the SL_Heaven Rails app). This is the
   **source of record**.
2. **ML model** — a LightGBM regressor trained on real historical quotes. This
   is a **data-driven second opinion** plus a likely-range, not the official price.

It quotes four services: **PCA Equity (1), ESA (2), Zoning (3), PCA Debt (4)**.

There are two front ends and one backend:

- **Copilot Studio agent** in Microsoft Teams (conversational) → Power Platform
  custom connector → Flask API.
- **Streamlit web app** ("Service Pricing Tool") → Flask API (+ direct warehouse
  fallback).
- **Flask API** ("PricePilot API") → rule engine + ML model + Keboola warehouse.

```
   User (Teams)                         Browser
        │                                  │
   Copilot Studio agent              Streamlit app  ── direct SQL fallback ──┐
        │  (natural language)              │                                 │
   Power Platform Custom Connector         │ HTTP                            │
   (OpenAPI 2.0)                           │                                 ▼
        └───────────────►  Flask API (Keboola python-js data app)  ──►  Keboola warehouse
                                 │            │              │            (Snowflake via
                       rule engine        ML model      client dir         Query Service /
                    (pricing_engine.py)  (.pkl bundle)  (SQL lookups)      Storage data-preview)
```

---

## 2. Repository map

| Path | What lives here |
|------|-----------------|
| `api.py` | **Flask REST API.** Deployed as the *python-js* Keboola data app "PricePilot API". Combines rule + ML + warehouse lookups. |
| `pricing_engine.py` | **Rule-based engine.** Pure-Python/pandas port of `Pricing.main_algo`. Imported by `api.py`. |
| `pricing_rules_calculator/main.py` | **Streamlit UI.** Deployed as the *streamlit* Keboola data app "Service Pricing Tool". |
| `pricing_rules_calculator/_build_deploy_source.py` | Build step that turns `main.py` into `_deploy_source.py` for streamlit deploy (see [§11](#11-deployment-model)). |
| `pricing_rules_calculator/_deploy_source.py` | **Generated** deploy artifact (git-ignored). Do not edit by hand. |
| `machine_learning/` | Model trainer, Keboola transformation script, artifact-sync, and committed metric/importance snapshots. |
| `connectors/` | OpenAPI 2.0 spec (`.yaml` + `.json`) for the Power Platform custom connector + an Adaptive Card + import guide. |
| `keboola-config/` | Nginx + Supervisord config for the python-js data-app runtime. |
| `docs/` | This file, the technical report, the Copilot Studio prompt, and feature references. |
| `pyproject.toml` | Python dependencies for the Flask API. |
| `.github/workflows/sync-model-artifacts.yml` | CI that keeps the committed model metric/importance snapshots in sync with Keboola. |

---

## 3. The Keboola project (data + hosting)

Everything runs inside one Keboola project.

- **Project:** `skills_metrics` — id **`10556`**
- **Branch id:** **`1297242`**
- **Snowflake account prefix in FQNs:** `SAPI_10556`

### 3.1 Tables (fully-qualified names used in SQL)

| Logical table | FQN | Used by | Notes |
|---|---|---|---|
| Pricing factors | `"SAPI_10556"."in.c-Pricing_Agent_Input_Data"."pricing_factors"` | rule engine (API + UI) | The coefficients that drive rule-based pricing. ≤73 rows per service. |
| Comparable projects | `"SAPI_10556"."out.c-pricing_ml"."comparable_projects"` | UI comparables, **client/client-type lookups** | ~128.7k rows total (last 3 years, fee > 0). ~24.3k rows / ~1,912 distinct clients for service 4. |
| Fee stats by service | `"SAPI_10556"."out.c-pricing_ml"."fee_stats_service_3yr"` | UI percentile band | p5 / median / p95 awarded fee per service. |
| Fee stats by service×property | `"SAPI_10556"."out.c-pricing_ml"."fee_stats_service_property_3yr"` | UI percentile band | Same, narrowed by primary property type. |
| Secondary property types | `"SAPI_10556"."out.c-pricing_ml"."secondary_property_types"` | UI dropdown | Distinct secondary types per service×primary (count ≥ threshold). |
| Feature importance | `"SAPI_10556"."out.c-pricing_ml"."fee_model_importance"` | UI model panel + CI snapshot | Gain-based importance of the deployed model. |
| Model metrics | `"SAPI_10556"."out.c-pricing_ml"."fee_model_metrics"` | UI model panel + CI snapshot | Hold-out accuracy (MAE / within-10% / R²). |
| Model runs | `"SAPI_10556"."out.c-pricing_ml"."fee_model_runs"` | UI version footer + CI snapshot | History of training runs. |
| ML training input | `in.c-pricing-data-transformation.pricing_fee_model_input` | trainer only | ~141k cleaned/winsorized historical quotes. |

The `pricing_factors` table id (as used by the **Storage** API, not SQL) is
`in.c-Pricing_Agent_Input_Data.pricing_factors`.

**Why a table, not code, drives pricing:** updating a coefficient is a *data*
change in `pricing_factors` — no code deploy. The rule engine reads it live
(cached, see below).

### 3.2 The ML model bundle

- Stored as a **Keboola Storage file tagged `pricepilot_fee_model`** (joblib).
- The API loads the newest file with that tag on first use and caches it
  in-memory until restart.
- Bundle is a dict: `model`, `features`, `numeric_features`,
  `categorical_features`, `missing_category`, `quantile_models` (p50/p85),
  `quantiles`.

---

## 4. Service + fee constants (must stay consistent across files)

Defined in both `pricing_engine.py` and `pricing_rules_calculator/main.py`:

| Service id | Name | Default base fee | ML-trained? |
|---|---|---|---|
| 1 | PCA Equity | $4,000 | yes (`service_type_id` 353) |
| 2 | ESA | $2,200 | yes (301) |
| 3 | Zoning | $2,500 | **no** — rule-based only |
| 4 | PCA Debt | $2,400 | yes (346) |

- API maps `order_form_service_id → service_type_id` via
  `ORDER_FORM_TO_SERVICE_TYPE_ID = {1:"353", 2:"301", 4:"346"}` in `api.py`.
  Zoning (3) is intentionally absent — the model was never trained on it, so the
  ML path raises a clear error and the rule-based result stands.
- UI default display order: `SERVICE_DISPLAY_ORDER = [4, 1, 2, 3]` (Debt first —
  it's the most common request).

> **Decision — duplicate the constants instead of sharing a module.** The
> Streamlit app and the API deploy as *separate* Keboola data apps with separate
> runtimes; keeping the small constant sets local avoids a shared-package
> dependency at deploy time. The trade-off is they must be kept in sync — change
> base fees / service ids in **both** `pricing_engine.py` and `main.py`.

---

## 5. Rule-based engine (`pricing_engine.py`)

A line-for-line port of the canonical Ruby `Pricing.main_algo`, operating on the
`pricing_factors` DataFrame. Entry point: `calculate(...)`. Key behaviors:

- **~12-category additive model** — base fee + per-category surcharges (limit of
  liability, turnaround, portfolio, travel difficulty, site complexity, prior
  report, size, units, buildings, stories, international, …), summed and rounded
  to the nearest $50.
- **Facility-type awareness** — size is driven by *building area (SF)* for most
  types, but by *total units + % to inspect* for Multi-Family / Seniors Housing.
- **Size-level precedence fix** — corrects a Ruby alphabetical-sort bug so sizes
  order `XS < S < M < L < XL < 2XL < 3XL` numerically.
- **RFP fallback** — a turnaround outside the supported window returns
  `is_rfp = true` instead of fabricating a number.
- **Per-TAT scale** — `_calc_tat_totals` can return the price at every turnaround
  day for "what if I had more time?" analysis.
- `breakdown_rows(result)` flattens the result into the per-line breakdown the
  API and UI render.

> **Decision — port the Ruby algorithm exactly rather than re-derive it.** The
> business already trusts `Pricing.main_algo`; an exact port makes the API a
> drop-in oracle whose numbers reconcile with the system of record. The same
> module is imported by the API so every surface returns identical rule-based
> numbers.

---

## 6. ML model (`machine_learning/`)

- **Algorithm:** LightGBM gradient-boosted trees with native categorical + NaN
  handling. **Target = `log_fee`**; predictions are exponentiated back to dollars
  and rounded to the nearest $50.
- **Range:** separate p50 / p85 quantile models give a likely-range
  (`predicted_low` / `predicted_high`). On right-skewed/premium jobs the point
  estimate reads low, so the range communicates the upside.
- **Leakage discipline:** cost/margin, final fee, status, and raw identifiers
  exist in the source table but are **excluded** as features (only known after
  award). See `LEAKAGE_EXCLUDED` / `ID_EXCLUDED` in the trainer.
- **Files:** `train_fee_model.py` (local/standalone),
  `keboola_fee_training_script.py` (runs as a Keboola Python transformation,
  needs `lightgbm` in packages), `sync_model_artifacts.py` (pulls metric tables
  back into the repo).
- **Artifact sync:** each retrain rewrites `out.c-pricing_ml.fee_model_importance`
  and `…fee_model_metrics` (the source of truth). CI
  (`.github/workflows/sync-model-artifacts.yml`) regenerates the committed
  `*.csv`/`*.md` snapshots daily, on demand, or on a `model-retrained`
  `repository_dispatch`. Requires repo secret `KBC_TOKEN`.

> **Decision — predict `log(fee)` with quantile companions.** Fees are
> right-skewed and span orders of magnitude; modeling the log stabilizes
> variance, and the p50/p85 models turn a single guess into an honest range
> without retraining the main model.

---

## 7. Flask API (`api.py`) — the backend that owns logic + data access

A single Flask app. **All shared business logic and data access should live here**
so every front end reuses one implementation (see [§9](#9-client--client-type-lookups-the-scalable-pattern)).

### 7.1 Endpoints

| Method / Path | Purpose |
|---|---|
| `GET /` | Service info + endpoint listing. |
| `GET /?api=true&...` | Rule + ML quote via query string. |
| `POST /quote` | Rule + ML quote via JSON body (primary). |
| `GET /services` | Services with default base fees + factor counts. |
| `GET /pricing-factors?service_id=` | Raw factor rules for a service. |
| `GET /clients?service_id=&search=&client_type=&limit=` | **Searchable, sorted client names**; `client_type` cross-filters. |
| `GET /client-types?service_id=&search=&client_name=&limit=` | **Searchable client types**; `client_name` scopes to that client's type(s) and sets `is_unique_for_client`. |
| `GET /health` | Liveness (does not load the model). |
| `GET /ready` | Readiness (factors load + model availability). |
| `GET /debug/ml-info`, `GET /debug/files` | Introspection. **Harden/restrict in prod.** |

Input parsing: `parse_input_row` normalizes params. `primary_property_type` is
the preferred field; `facility_type` is a backward-compatible alias. Likewise
`client_type` is an alias for `customer_type`.

Response shape (quote): `{ inputs, rule_based, ml, ml_error, comparison, results,
predicted_fee, predicted_multiplier }`. The app **degrades gracefully**: if the
model can't load, `ml_error` is set and the rule-based result still returns.

### 7.2 Two different warehouse-access mechanisms (important)

The API talks to Keboola **two ways**, on purpose:

1. **Storage `data-preview` API** (`load_factors_for_service`) — used for
   `pricing_factors`. Needs only `KBC_TOKEN` (+ `KBC_URL`). **Capped at ~100
   rows** and cannot aggregate, but each service has ≤73 factor rows, so a
   `whereColumn` filter fits comfortably. Cheap and simple.
2. **Query Service SQL** (`run_sql`) — used for the **client directory**
   (`/clients`, `/client-types`). Needs `BRANCH_ID`, `WORKSPACE_ID`, `KBC_TOKEN`,
   `KBC_URL`. Runs a real SQL query against a workspace and paginates the full
   result (1000 rows/page). Required because `comparable_projects` is ~128k rows
   and the lookups need `GROUP BY` / `DISTINCT` — far beyond `data-preview`.

> **Decision — keep `data-preview` for factors, add Query Service for client
> lookups.** Don't pay the workspace/SQL complexity for the tiny factors table;
> don't cripple the large client lookups with the 100-row preview cap. Use the
> cheapest mechanism that fits each job.

> ⚠️ **Operational gotcha:** the python-js API app historically only had
> `KBC_TOKEN`, so the **Query Service path fails until the app has
> `WORKSPACE_ID` + `BRANCH_ID`** set as data-app secrets. See
> [§11.3](#113-known-deployment-gotchas).

### 7.3 Caching

- Pricing factors: per-service, TTL `PRICING_FACTORS_TTL_SECONDS` (default 600s).
- Client directory: per-service `pandas` frame, TTL `CLIENT_DIRECTORY_TTL_SECONDS`
  (default 900s). Loaded **once** then filtered/sorted/searched in-process, so
  dropdown interactions never re-scan the warehouse.
- ML bundle: loaded once, cached until restart.

---

## 8. Streamlit UI (`pricing_rules_calculator/main.py`)

A browser front end deployed as a Keboola *streamlit* data app.

- Imports the **same** `pricing_engine` logic (vendored into the file) so its
  rule-based numbers match the API exactly.
- Calls the Flask API for the ML prediction (`call_ml_api`, base URL
  `ML_API_URL` / `ML_API_URL_DEFAULT`).
- Has its **own** warehouse access via an injected `query_data` function (see
  [§11.1](#111-streamlit-deploy-inline-source--query_data-injection)).
- Renders: inputs → rule-based total + breakdown, ML point + range, comparison,
  percentile band, comparable past projects, and model-accuracy/importance panels.

`query_data` uses the Query Service with `WORKSPACE_ID` (`2950783790`) +
`BRANCH_ID` (`1297242`) secrets — which is why the UI can fall back to direct SQL
when the API lookups aren't available.

---

## 9. Client / client-type lookups (the scalable pattern)

This is the most recent feature and the **reference example** for how to add
data-backed dropdowns. Requirements were: searchable, sorted, client name first,
selecting a client sets/scopes its client type, and **vice-versa** (selecting a
type filters the client list).

**Key data fact:** client type is **NOT unique per client.** For service 4, ~9,126
clients map to a single type but ~1,480 span 2–15 types (verified by query). So a
client can't simply be mapped to "its type" — all of a client's types must be
offered (most-common first; auto-selected only when there is exactly one).

### 9.1 Where the logic lives — and why

- **Backend owns it** (`api.py`): `load_client_directory` (cached per-service
  `(client_name, customer_type, n)` frame) + `query_clients` / `query_client_types`
  do the filtering, sorting, and **bidirectional** cross-filtering. Exposed as
  `/clients` and `/client-types`.
- **Streamlit is a thin client**: `fetch_clients_api` / `fetch_client_types_api`
  call the endpoints (cached), with `get_client_options` / `get_client_type_options`
  falling back to direct `query_data` if the API is unreachable.
- **Connector** documents both endpoints (`ListClients`, `ListClientTypes`).

> **Decision — put lookup logic in the API, not Streamlit.** Asked explicitly to
> design for the long term ("what if I add more of these? what if I move to AWS?
> what's faster and scalable?"). Reasons:
> 1. **Reuse** — Streamlit, Copilot Studio, and any future host share one
>    implementation; add the next lookup once.
> 2. **Speed/scale** — the API aggregates a small per-service directory **once**
>    and caches it, so searches/filters run in-process instead of re-scanning
>    128k rows per keystroke.
> 3. **Portability** — moving to AWS is re-hosting one Flask service (or pointing
>    the directory at RDS/DynamoDB); no UI rewrite.
> The Streamlit fallback exists only so the tool keeps working if the API is
> mid-deploy or missing the workspace secrets — it is not the primary path.

### 9.2 Bidirectional linking (Streamlit specifics)

Two-way linked selects in a top-to-bottom rerun framework can oscillate. The
implementation avoids that by: rendering **client name first**, reading the type
widget's current value to filter the client list in the *same* run, then dropping
any selection the counterpart filter excludes (so a stale value never crashes the
widget or ping-pongs). Selecting a client with one type auto-selects it; with
several, all are listed most-common-first.

---

## 10. Integration layer — Copilot Studio + connector

- **`connectors/pricepilot-api.openapi.yaml` / `.json`** define a Power Platform
  custom connector (OpenAPI **2.0 / Swagger**, because that's what Power Platform
  consumes). Operations: `GetQuote`, `ListServices`, `GetPricingFactors`, and now
  `ListClients`, `ListClientTypes`.
- **No authentication** — the API is open so the connector and Streamlit can call
  it without credentials; it only ever returns pricing estimates (no PII).
- The agent prompt lives in `docs/copilot-instructions.md` (kept under Copilot
  Studio's 8 KB limit). It handles guided field collection, response formatting,
  RFP/error special-casing, and **cold-start mitigation** (silent warm-up call +
  automatic retry, because the data app sleeps after idle).

> **When you add/rename an API endpoint or field, update BOTH OpenAPI files** (the
> `.yaml` and the `.json` are maintained in parallel) or the connector drifts.

---

## 11. Deployment model

Two **separate** Keboola data apps. They deploy **differently** — this trips
people up, so read carefully.

| | Streamlit UI | Flask API |
|---|---|---|
| Name | Service Pricing Tool | PricePilot API |
| Type | `streamlit` | `python-js` |
| Config id | `01ksk8pfe5n2020x6eba3tzd8h` | `01ksn97z3majzt0cr8xytex9by` |
| Data-app id | `1304626179` | `1304626184` |
| URL | `service-pricing-tool-1304626179.hub.keboola.com` | `pricepilot-api-1304626184.hub.keboola.com` |
| Source of truth | **Inline** in the config | **This Git repo's `main`** |
| Packages | httpx, pandas, requests | `pyproject.toml` |
| Auth | no-auth | no-auth |
| Auto-suspend | 900s | 3600s |

### 11.1 Streamlit deploy (inline source + `query_data` injection)

The streamlit app has **no git repo**; its Python is stored inline in the config.
Deploy steps:

1. `python pricing_rules_calculator/_build_deploy_source.py` — replaces the
   `# ### INJECTED_CODE #### … # ### END_OF_INJECTED_CODE ####` block in `main.py`
   with the `{QUERY_DATA_FUNCTION}` placeholder and writes `_deploy_source.py`.
2. Push `_deploy_source.py`'s contents as the data app's `source_code` (Keboola
   provides/injects its own `query_data` at the placeholder), then **redeploy**.

> **Decision — `{QUERY_DATA_FUNCTION}` placeholder.** Keboola injects a
> platform-managed `query_data` (workspace creds wired in) at deploy time, so the
> app never hard-codes warehouse credentials. `main.py` keeps a real `query_data`
> between the markers only so it runs/lints locally; the build step swaps it out.

### 11.2 Flask API deploy (git)

The python-js app's `repo_url` **is this GitHub repo**
(`github.com/SamanollahiPartneresi/Pricing-API-and-more`), branch `main`. So:

1. Commit + push your changes to `main`.
2. Redeploy the API data app (it re-clones `main` on restart).

### 11.3 Known deployment gotchas

- **API Query Service access:** `/clients` and `/client-types` return
  `RuntimeError: Missing env vars for SQL access: BRANCH_ID, WORKSPACE_ID, …`
  until the **PricePilot API** data app has `WORKSPACE_ID` and `BRANCH_ID` added
  as **secrets** (the Streamlit app already has them: `WORKSPACE_ID=2950783790`,
  `BRANCH_ID=1297242`). This must be done in the **Keboola UI** — the MCP
  data-app tools don't expose secrets, and `update_config` is disallowed for
  `keboola.data-apps`. Caveat: the API app's auto-injected `KBC_TOKEN` must have
  access to that workspace; if not, provision a workspace for the API app.
- **Streamlit inline size:** the deployable source is ~2,500 lines; pushing it
  through tooling is large. Always verify the stored source compiles and contains
  the new code **before** redeploying so a truncated/garbled push can't take the
  live UI down.
- **Model-artifact CI commits to `main`:** `sync-model-artifacts.yml` pushes
  `[skip ci]` commits, so `git push` may need a `git pull --rebase` first.

---

## 12. Environment variables

| Var | Used by | Purpose |
|---|---|---|
| `KBC_TOKEN` | API, UI, CI | Keboola Storage API token. |
| `KBC_URL` | API, UI | Keboola stack URL (default `https://connection.keboola.com`). |
| `BRANCH_ID` | API (SQL), UI | Branch id `1297242` (Query Service). |
| `WORKSPACE_ID` | API (SQL), UI | Workspace id `2950783790` (Query Service). |
| `ML_API_URL` | UI | Flask API base URL (defaults to the prod API). |
| `FEE_MODEL_TAG` | API | Storage file tag for the model bundle (`pricepilot_fee_model`). |
| `PRICING_FACTORS_TABLE_ID` | API | Storage table id for factors. |
| `COMPARABLE_PROJECTS_TABLE` | API | FQN for the client directory source. |
| `PRICING_FACTORS_TTL_SECONDS` / `CLIENT_DIRECTORY_TTL_SECONDS` | API | Cache TTLs (600 / 900). |
| `DEFAULT_ORDER_FORM_SERVICE_ID` | API | Default service when omitted (4). |
| `FEE_INPUT_PATH` / `FEE_N_JOBS` | trainer | Local training input path / LightGBM thread count. |

---

## 13. Where to start for common changes

| I want to… | Start here |
|---|---|
| Change a pricing coefficient / fee tier | **Data only:** edit the `pricing_factors` table in Keboola. No deploy. |
| Change rule-based math / add a category | `pricing_engine.py` (`calculate` + the relevant `resolve_*`). Mirror any constant in `main.py`. |
| Add/Change an API endpoint | `api.py` (route + helper), then **both** `connectors/*.openapi.{yaml,json}`. |
| Add another searchable/cross-filtered dropdown | Copy the [§9](#9-client--client-type-lookups-the-scalable-pattern) pattern: directory loader + `query_*` helper + endpoint in `api.py`; thin `fetch_*`/`get_*` + widget in `main.py`; document in the connector. |
| Change the UI layout / inputs | `pricing_rules_calculator/main.py`, then rebuild + redeploy ([§11.1](#111-streamlit-deploy-inline-source--query_data-injection)). |
| Retrain / swap the ML model | `machine_learning/` (`train_fee_model.py` or the Keboola transform); tag the bundle `pricepilot_fee_model`; restart the API. |
| Change service ids / base fees | `pricing_engine.py` **and** `main.py` (+ `ORDER_FORM_TO_SERVICE_TYPE_ID` if ML coverage changes). |
| Change the agent's behavior | `docs/copilot-instructions.md`. |

---

## 14. Guiding principles (the "why" in one place)

1. **Two estimates, one truth.** Always show rule-based *and* ML, but the
   rule-based total is the source of record; ML is a comparison + range.
2. **Pricing is data, not code.** Coefficients live in `pricing_factors` so
   pricing changes don't require a deploy.
3. **Logic lives in the backend.** Shared/data-backed logic goes in the Flask API
   so every front end reuses it and the system stays portable (e.g., to AWS).
   Front ends are thin; the Streamlit direct-SQL path is only a fallback.
4. **Use the cheapest data mechanism that fits.** Storage `data-preview` for tiny
   tables; Query Service SQL for large/aggregated lookups; cache aggressively.
5. **Degrade gracefully.** A missing model, a sleeping app, or an unreachable API
   should never produce a blank page or a fabricated number — fall back, retry,
   or clearly flag `is_rfp` / `ml_error`.
6. **Keep parallel artifacts in sync.** Constants in two apps, OpenAPI in two
   formats, model snapshots vs. Keboola tables — change them together.
