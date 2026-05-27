# Copilot Studio — PricePilot instructions

Paste into your agent system prompt. Use the **Flask API** Data App URL with **Basic Auth** (not Streamlit).

## API URL pattern

```
https://YOUR-PRICEPILOT-API-URL.hub.keboola.com/?api=true&base_fee={value}&tat={value}&building_area={value}&number_of_stories={value}&facility_type={value}
```

## Required fields (collect one at a time)

- `base_fee` — starting fee in USD (number)
- `tat` — turnaround days (integer)
- `building_area` — square feet (number)
- `number_of_stories` — integer
- `facility_type` — text (e.g. Office, Industrial)

## Response

Use `results.total_fee` or `predicted_fee` from JSON. On `"error"` in response, say the model encountered an error and suggest contacting the data team.

## Full prompt

See the agent instruction block in the project README or your Copilot configuration export.
