# PricePilot Custom Connector for Copilot Studio

This folder contains the OpenAPI 2.0 (Swagger) definition that turns the live
PricePilot Flask API into a Power Platform **Custom Connector** that your
Copilot Studio agent can call as a native action.

Files:

- `pricepilot-api.openapi.yaml` - the connector definition (OpenAPI 2.0 — required by Power Platform).

Live API base URL:

```text
https://pricepilot-api-1304626184.hub.keboola.com
```

The connector exposes four actions:

| Action (operationId) | Purpose |
| --- | --- |
| `ListServices`        | Get all 4 services with default base fees + factor counts |
| `GetPricingFactors`   | Get the raw factor rules for one service ("explain my quote") |
| `GetQuote`            | Run the rule-based engine + ML model and return both totals plus a comparison block |
| `HealthCheck`         | Liveness probe (marked internal) |

---

## Part 1 — Import the connector into Power Platform

You only do this once per environment.

1. **Sign in** to <https://make.powerautomate.com> (or <https://make.powerapps.com>).
2. In the left sidebar, expand **More** -> **Discover all** -> **Custom connectors** (or just go to <https://make.powerautomate.com/customconnectors>).
3. Click **+ New custom connector** -> **Import an OpenAPI file**.
4. Give it a name: **`PricePilot Pricing`**.
5. Click **Import** and pick `pricepilot-api.openapi.yaml` from this folder.
6. Click **Continue**.
7. On the **General** tab:
   - Host: `pricepilot-api-1304626184.hub.keboola.com` (pre-filled)
   - Base URL: `/`
   - Scheme: `HTTPS`
   - (Optional) Upload an icon and pick a background colour.
8. On the **Security** tab:
   - Authentication type: **No authentication** (the data app is open by design so Copilot can reach it without credentials).
9. **Definition** tab — leave as-is; the YAML already has every action wired up.
10. Click **Create connector** in the top-right.

### Test it before wiring into Copilot

1. Switch to the **Test** tab.
2. Click **+ New connection** and confirm (no credentials needed).
3. Pick the **GetQuote** operation.
4. Fill in a quick test body:

   ```json
   {
     "order_form_service_id": 4,
     "base_fee": 2400,
    "tat": 10,
    "primary_property_type": "Office",
    "building_area": 80000,
     "number_of_stories": 3,
     "number_of_buildings": "2",
     "country_code": "US",
     "travel_difficulty": "< 60 minute drive"
   }
   ```

5. Click **Test operation**. You should get a 200 with `rule_based`, `ml`, and `comparison` blocks.

If that returns `RuleBasedTotal: 3450` and a populated `comparison.delta_pct`, the connector is good.

---

## Part 2 — Add the connector to your Copilot Studio agent

1. Open <https://copilotstudio.microsoft.com> and select your existing agent.
2. In the left rail go to **Actions** (under Agents) -> **+ Add an action**.
3. In the picker, choose **Connector** -> search for **PricePilot Pricing** -> **Add**.
4. Pick the operations you want the agent to use. Recommended set:
   - **GetQuote** (the headline action)
   - **ListServices**
   - **GetPricingFactors**
5. For each operation, Copilot Studio will auto-generate parameter inputs from the OpenAPI. You can:
   - **Mark each parameter as filled by AI** so the agent extracts them from the user's natural-language question, or
   - Bind them to topic variables for a structured flow.
6. Click **Save**.

### Suggested system prompt addition

Paste this into your agent's instructions so it knows when to reach for the new action:

```text
You can call the PricePilot Pricing connector to quote commercial property services.

- Use ListServices when the user asks "what services do you offer" or "what can you price".
- Use GetQuote whenever the user wants a price/estimate/quote. At minimum you need
  order_form_service_id (1=PCA Equity, 2=ESA, 3=Zoning, 4=PCA Debt) and tat (turnaround
  days). Ask the user for any missing primary_property_type (alias facility_type), building_area, country_code,
  travel_difficulty before quoting. Always show BOTH the rule_based.total_fee and
  ml.predicted_fee, and mention the comparison.delta_pct.
- Use GetPricingFactors when the user asks "why is the price what it is" or
  "what affects the fee" — pass the service_id from the most recent quote.
- If rule_based.is_rfp is true, tell the user the turnaround they asked for falls
  outside our standard pricing window and a custom proposal (RFP) is required.
```

### Test the agent

Try these natural-language prompts:

| Prompt | Expected action |
| --- | --- |
| "Can you quote a PCA Debt review for an 80,000 sf office building in the US, two buildings, 10 day turnaround?" | `GetQuote` with the right `order_form_service_id=4` |
| "What services do you have?" | `ListServices` |
| "What factors drive the PCA Equity price?" | `GetPricingFactors` with `service_id=1` |

---

## Notes and gotchas

- **OpenAPI 2.0, not 3.x.** Power Platform Custom Connectors still require Swagger 2.0. The included file is already 2.0.
- **`rule_based.total_fee` can be a string `"RFP"` or a number.** The agent should be ready to render either.
- **The ML and rule-based predictions can disagree dramatically** for inputs the ML model wasn't trained well on. The `comparison` block is there so the agent can disclose the gap honestly.
- **Auto-suspend.** The Keboola data app suspends after 15 minutes of no traffic and cold-starts on the next request (~5-10 sec). If you want zero cold-starts, set `autoSuspendAfterSeconds` to 0 on the data-app config.
- **Updating the connector** after API changes: edit this YAML, then in the Custom Connector UI choose **Update connector** -> **Import an OpenAPI file** -> re-upload.

---

## When you'd outgrow this

If later you add several more APIs and want them all callable as a unified set of agent tools, consider building a small **Custom MCP server** that fronts all of them. For just this one API, the Custom Connector is the lighter, faster, more maintainable choice.
