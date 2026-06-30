# PricePilot / Service Pricing Tool — Architecture

> **Read this first.** This is the single source of truth for how the whole
> pricing system is wired together: the components, the data, the deployment
> model, and — importantly — **why** each decision was made. If you're coming
> back to make a change, jump to [§13 "Where to start for common changes"](#13-where-to-start-for-common-changes).

Last updated: 2026-06-30.

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
| `pricing_engine.py` | **Rule-based engine.** Pure-Python/pandas port of `Pricing.main_algo`. **Single source of truth** — imported at runtime by both `api.py` and `pricing_rules_calculator/main.py`. |
| `pricing_rules_calculator/main.py` | **Streamlit UI.** Deployed as the *streamlit* Keboola data app "Service Pricing Tool", **straight from this Git repo** (entrypoint). Imports the rule engine from `pricing_engine.py`. |
| `requirements.txt` | Runtime deps for the Streamlit app's **Git deploy** (streamlit, pandas, httpx, requests). |
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

For **how each table is built** (the Keboola transformations + data lineage), see
[§3.3](#33-keboola-data-pipeline-transformations--lineage).

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

### 3.3 Keboola data pipeline (transformations + lineage)

All the warehouse tables above are produced by **three transformations** in the
Keboola project. **There are no orchestration flows** — the transformations are
run individually/manually (or scheduled), not chained. This is the part the app
code does *not* show, so it's documented here in full.

```
SL_Heaven Service Details UI (manual transcription)
        │
        ▼
[Snowflake] "Seed pricing_factors …" ──► in.c-Pricing_Agent_Input_Data.pricing_factors   (rule engine reads this)

(upstream load — extractor/external, NOT a transformation in this project)
        │
        ▼
in.c-pricing-data-transformation.pricing_fee_model_input  (~141k historical quotes; siblings: pricing_fee_comparables, pricing_fee_training)
        │
        ├──► [Snowflake] "Fee stats by service and property" ──► out.c-pricing_ml.fee_stats_service_3yr
        │                                                        out.c-pricing_ml.fee_stats_service_property_3yr
        │                                                        out.c-pricing_ml.secondary_property_types
        │                                                        out.c-pricing_ml.comparable_projects   (client/type lookups read this)
        │
        └──► [Python] "pricepilot_fee_model_training" ──► file: pricepilot_fee_model.pkl  (tag: pricepilot_fee_model → API loads)
                                                          out.c-pricing_ml.fee_model_metrics
                                                          out.c-pricing_ml.fee_model_importance
                                                          out.c-pricing_ml.fee_model_runs  (incremental, PK run_id)
```

**Transformation 1 — `Seed pricing_factors with PROD PCA Equity + ESA + Zoning demo + PCA Debt`**
- Component / id: `keboola.snowflake-transformation` / `01ksnp4b6qjcgcfpxe45dvrdsq`
- Input: none. Output: `pricing_factors` → **`in.c-Pricing_Agent_Input_Data.pricing_factors`** (`CREATE OR REPLACE`, full rewrite, non-incremental).
- It's a single hand-written `SELECT … UNION ALL` of **257 literal rows** transcribed verbatim from the SL_Heaven Service Details Management UI:
  - service 1 (PCA Equity, PLINK 353): factor_id 1–73
  - service 2 (ESA, PLINK 301): 74–146
  - service 3 (Zoning **demo**, 38 rows): 147–184 — placeholder until real Zoning factors exist
  - service 4 (PCA Debt, PLINK 346): 185–257
- Columns: `factor_id, category, level, description, value, default_flag, order_form_service_id`.
- **Why it exists / gotcha:** production factors live as data, and this supersedes the old `*_PRICING_FACTORS_DEFAULTS` code constants that had drifted. Because it's `CREATE OR REPLACE`, **any manual edit to the `pricing_factors` table is wiped the next time this runs** — change pricing by editing this transformation's SQL (or the table, knowing a re-run overwrites it).

**Transformation 2 — `Fee stats by service and property`**
- Component / id: `keboola.snowflake-transformation` / `01kt4z779zk226f0npsxe39ryq`
- Input: `in.c-pricing-data-transformation.pricing_fee_model_input`.
- Four SQL blocks → four outputs in `out.c-pricing_ml`: `fee_stats_service_3yr`,
  `fee_stats_service_property_3yr`, `secondary_property_types` (count ≥ 10),
  and **`comparable_projects`** (the table the Streamlit comparables panel **and
  the `/clients` + `/client-types` lookups** read).
- Common filters: `fee > 0`, `created_date` within the **last 3 years**, services
  `353/301/346` only. Maps `service_type_id`/name → `order_form_service_id`
  (353→1, 301→2, 346→4). **Zoning (3) is absent from these ML tables.**
- **Why:** precomputes the small/derived tables the UI and lookups need so neither
  has to scan or aggregate the raw quotes table at request time.

**Transformation 3 — `pricepilot_fee_model_training`**
- Component / id: `keboola.python-transformation-v2` / `01kt2pa41qm3ehcjprzzrvr00a`
- Input: `in.c-pricing-data-transformation.pricing_fee_model_input`. Packages:
  `lightgbm 4.6.0`, `scikit-learn 1.4.2`, `pandas 2.1.4`, `numpy 1.26.4`,
  `scipy 1.13.1`, `joblib 1.3.2`.
- Outputs: the **`pricepilot_fee_model.pkl`** file (permanent, tag
  `pricepilot_fee_model` — this is exactly what `api.py` loads), plus
  `fee_model_metrics`, `fee_model_importance`, and `fee_model_runs` (incremental,
  PK `run_id`) in `out.c-pricing_ml`.
- Trains LightGBM on `log_fee` with the rule-aligned feature set (numeric:
  `turn_around_time, building_area, land_acreage, total_units, pct_units_inspect,
  number_of_stories, number_of_buildings, created_month`; categorical:
  `service_type_id, primary_property_type, secondary_property_type, prior_report,
  site_complexity, country, customer_type`), 80/20 split stratified by service,
  Duan smearing correction, and p50/p85 quantile models for the range.
- **This is the deployed copy of** `machine_learning/keboola_fee_training_script.py`
  — keep the two in sync.

### 3.4 Raw input bucket (`in.c-pricing-data-transformation`)

The historical-quotes source layer. Tables: `pricing_fee_model_input` (the one
the trainer + fee-stats read), plus `pricing_fee_comparables` and
`pricing_fee_training`. **No transformation in this project produces these** —
they're loaded upstream (an extractor / external load / shared bucket), so their
genesis lives outside this repo and project's transformation list.

---

## 4. Service + fee constants (must stay consistent across files)

Defined **once** in `pricing_engine.py` (imported by both `api.py` and `main.py`):

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

> **Decision — one engine module, imported at runtime by both apps (was:
> hand-duplicated, then build-time inlined).** `pricing_engine.py` is the
> **single source of truth**. Both the API and the Streamlit app now deploy from
> this Git repo, so both simply `import pricing_engine` at runtime (the Streamlit
> app's `main.py` adds the repo root to `sys.path` so the import resolves whether
> run locally or from the cloned repo — see [§11.1](#111-streamlit-deploy-from-git)).
> No copy, no inlining, no drift. Change base fees / service ids / rule math in
> `pricing_engine.py` **only**.

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
> module is imported at runtime by **both** the API and the Streamlit UI (see
> [§11.1](#111-streamlit-deploy-from-git)) so every surface returns identical
> rule-based numbers.

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

A browser front end deployed as a Keboola *streamlit* data app, **straight from
this Git repo** (entrypoint `pricing_rules_calculator/main.py`, branch `main`).

- Imports the rule engine from `pricing_engine.py` — the **same** module the API
  uses — so its rule-based numbers match the API exactly. Because the app deploys
  from the repo, the import just works at runtime (see [§11.1](#111-streamlit-deploy-from-git));
  no inlining, no generated copy.
- Calls the Flask API for the ML prediction (`call_ml_api`, base URL
  `ML_API_URL` / `ML_API_URL_DEFAULT`).
- Has its **own** warehouse access via a built-in `query_data` function (uses the
  Query Service with the app's secrets).
- Renders: inputs → **two side-by-side panels** (rule-based total + breakdown +
  turnaround on the left; ML point + range + accuracy + feature importance on the
  right), then full-width comparable past projects, percentile band, and
  provenance.

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
| Source of truth | **This Git repo's `main`** | **This Git repo's `main`** |
| Entrypoint | `pricing_rules_calculator/main.py` | repo root (`api.py`) |
| Packages | `requirements.txt` | `pyproject.toml` |
| Auth | no-auth | no-auth |
| Auto-suspend | 28800s (8h) | 3600s |

### 11.1 Streamlit deploy (from Git)

As of 2026-06-30 the Streamlit app deploys **from this Git repo**, exactly like
the API — no more inline source, no build step, no copy-paste. Keboola supports
Git deployment for `streamlit` data apps too: it clones the repo and runs the
entrypoint with `streamlit run`.

**Config (Keboola UI → data app → Code Source → Git Repository):**

- **Project URL:** `https://github.com/SamanollahiPartneresi/Pricing-API-and-more`
- **Branch:** `main`
- **Main entrypoint file:** `pricing_rules_calculator/main.py`
- **Private repo auth:** GitHub username + a fine-grained PAT scoped to this repo
  with **Contents: Read-only** (or an SSH deploy key).
- **Packages:** repo-root `requirements.txt` (streamlit, pandas, httpx, requests).
- **Secrets** (unchanged): `WORKSPACE_ID=2950783790`, `BRANCH_ID=1297242`;
  `KBC_TOKEN`/`KBC_URL` are auto-injected.

**Update workflow:** edit `main.py` (and `pricing_engine.py` if needed) → commit
+ push to `main` → **Redeploy** the app (it re-clones `main`). That's it.

`main.py` runs as the entrypoint as-is: it inserts the repo root on `sys.path`
and `import pricing_engine`, and defines its own `query_data` against the Query
Service. There is **no `{QUERY_DATA_FUNCTION}` placeholder** — that substitution
only ever applied to the `modify_streamlit_data_app` MCP tool, which we no longer
use for this app.

> **Decision — deploy Streamlit from Git, not inline paste (changed 2026-06-30).**
> The old workflow stored the app's Python inline in the config and required
> building a **self-contained** `_deploy_source.py` (engine inlined, real
> `query_data` kept, no placeholder) and pasting it into the UI — fragile (a
> garbled paste / a stray `{QUERY_DATA_FUNCTION}` placeholder once took the live
> UI down) and divergent from the API's git workflow. Git deployment removes the
> build script, the generated artifact, and the paste step entirely: one workflow
> for both apps, `pricing_engine.py` imported (not inlined), and changes ship by
> pushing to `main`. See [§4](#4-service--fee-constants-must-stay-consistent-across-files).

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
- **Streamlit git deploy — entrypoint & deps:** the entrypoint path
  (`pricing_rules_calculator/main.py`) is **relative to the repo root**, and the
  repo-root `requirements.txt` must include `streamlit` (≥1.49, for
  `st.columns(border=…)` / `st.dataframe(width="stretch")`), `pandas`, `httpx`,
  `requests`. A missing dep or wrong entrypoint shows up in the deploy logs
  (`deployment_info.logs` via `get_data_apps`), not the running app.
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
| Change a pricing coefficient / fee tier | Edit the **`Seed pricing_factors …`** Snowflake transformation's SQL and re-run it (it `CREATE OR REPLACE`s the table, so editing the table directly is overwritten on the next run). No code deploy. See [§3.3](#33-keboola-data-pipeline-transformations--lineage). |
| Rebuild comparables / fee-stats / secondary types | Re-run the **`Fee stats by service and property`** transformation ([§3.3](#33-keboola-data-pipeline-transformations--lineage)); these feed the comparables panel and the client/type lookups. |
| Change rule-based math / add a category | `pricing_engine.py` (`calculate` + the relevant `resolve_*`) — the **only** copy; both `api.py` and `main.py` import it at runtime. No mirroring. |
| Add/Change an API endpoint | `api.py` (route + helper), then **both** `connectors/*.openapi.{yaml,json}`. |
| Add another searchable/cross-filtered dropdown | Copy the [§9](#9-client--client-type-lookups-the-scalable-pattern) pattern: directory loader + `query_*` helper + endpoint in `api.py`; thin `fetch_*`/`get_*` + widget in `main.py`; document in the connector. |
| Change the UI layout / inputs | `pricing_rules_calculator/main.py`, then push to `main` + redeploy ([§11.1](#111-streamlit-deploy-from-git)). |
| Retrain / swap the ML model | Re-run the **`pricepilot_fee_model_training`** Keboola transformation (or `machine_learning/train_fee_model.py` locally); it writes the `pricepilot_fee_model`-tagged bundle; restart the API to pick it up. See [§3.3](#33-keboola-data-pipeline-transformations--lineage). |
| Change service ids / base fees | `pricing_engine.py` **only** (both apps import `SERVICE_NAMES`/`SERVICE_BASE_FEES`); also `ORDER_FORM_TO_SERVICE_TYPE_ID` in `api.py` if ML coverage changes. |
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
6. **One source of truth; import it everywhere.** The rule engine + its constants
   live once in `pricing_engine.py` — both `api.py` and the Streamlit `main.py`
   import it at runtime (both deploy from this repo), so there's no
   hand-maintained or generated duplicate to drift. Where parallel artifacts are
   unavoidable (OpenAPI in two formats, model snapshots vs. Keboola tables),
   change them together.
