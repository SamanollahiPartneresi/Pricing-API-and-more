# PricePilot — Pricing Features Reference

A complete map of every feature used for pricing, and **where to find each in the data**.
There are two sources:

- **A. Quote inputs** — the order-form fields a user provides (from SL_Heaven).
- **B. Pricing-factor categories** — the surcharge rules (from the Keboola `pricing_factors` table).

---

## A. Quote input features (order-form fields that drive a price)

These come from the **Order Form** (SL_Heaven). The engine uses these exact concepts:

| # | Feature (engine name) | Meaning | Applies to |
|---|---|---|---|
| 1 | `order_form_service_id` | Service: 1=PCA Equity, 2=ESA, 3=Zoning, 4=PCA Debt | all |
| 2 | `base_fee` | Service base price (`OrderFormService.base_price`) | all |
| 3 | `tat` | Turnaround time, business days | all |
| 4 | `building_area` | Building size (SF) | most property types |
| 5 | `total_units` | Number of units | Multi-Family, Seniors Housing |
| 6 | `percent_units_to_inspect` | % of units inspected (0–100, step 5) | Multi-Family, Seniors Housing |
| 7 | `land_area` | Land area (acres) | ESA |
| 8 | `number_of_buildings` | Building count | most types |
| 9 | `number_of_stories` | Story count | all |
| 10 | `primary_property_type` | Primary property type (Office, Industrial, …) — API field `facility_type` accepted as alias | all |
| 11 | `secondary_property_type` | Secondary property type | optional |
| 12 | `country_code` | US / CA (drives "International") | all |
| 13 | `travel_difficulty` | Travel difficulty level | all |
| 14 | `site_complexity` | Simple / Average / Complicated | all |
| 15 | `prior_report` | Prior report status | all |
| 16 | `limit_of_liability` | Limit of liability ($) | all |
| 17 | `portfolio_size` | # of properties in portfolio | all |

---

## B. Pricing-factor categories (the surcharge rules)

These live in the reference table **`in.c-Pricing_Agent_Input_Data.pricing_factors`**
(columns: `order_form_service_id`, `category`, `level`, `description`, `value`).
Level counts per service (confirmed from the live table):

| Category | Driven by input | PCA Equity (1) | ESA (2) | Zoning (3) | PCA Debt (4) |
|---|---|---|---|---|---|
| Turnaround Time | `tat` | 20 | 20 | 4 | 20 |
| Size | `building_area` | 7 | 7 | 5 | 7 |
| Travel Difficulty | `travel_difficulty` | 7 | 7 | 4 | 7 |
| Portfolio Size | `portfolio_size` | 6 | 6 | 4 | 6 |
| Prior Report | `prior_report` | 5 | 5 | 4 | 5 |
| Unit Inspection | `total_units` + `percent_units_to_inspect` | 5 | 5 | — | 5 |
| Limit of Liability | `limit_of_liability` | 4 | 4 | 4 | 4 |
| Site Complexity | `site_complexity` | 4 | 4 | 4 | 4 |
| # of Buildings | `number_of_buildings` | 4+5* | 4+5* | 3 | 4+5* |
| # of Stories | `number_of_stories` | 3 | 3 | 3 | 3 |
| International | `country_code` (non-US) | 2 | 2 | 2 | 2 |
| Time Period | (date/season — automatic) | 1 | 1 | 1 | 1 |

\* For PCA Equity / ESA / PCA Debt, "# of Buildings" is split into two facility-type
variants (`# of Buildings 1` and `# of Buildings 2`); Zoning uses a single `# of Buildings`.

---

## Where to find each in your databases

- **Inputs (Table A)** → your **order form / `order_form_services`** tables in SL_Heaven.
- **Factor rules (Table B)** → the Keboola `pricing_factors` table; in SL_Heaven these come
  from the pricing-factor configuration behind `Pricing.main_algo`.
- **Zoning (service 3)** is leaner — no Unit Inspection, fewer Turnaround/Size levels.
- **Time Period** has no order-form input — it's an automatic busy-season adjustment.

### Handy query (distinct categories per service)
```sql
SELECT "order_form_service_id", "category", COUNT(*) AS "level_count"
FROM "in.c-Pricing_Agent_Input_Data"."pricing_factors"
GROUP BY "order_form_service_id", "category"
ORDER BY "order_form_service_id", "category";
```
