# PricePilot — One-Page Overview

**An AI pricing assistant that delivers instant project-fee estimates inside Microsoft Teams.**

PricePilot lets anyone request a fee estimate in plain language ("ESA for a 50,000 SF office, 10-day turnaround") and get back two numbers side by side — a **rule-based** quote from our canonical pricing algorithm and a **machine-learning** estimate — in seconds, without opening a spreadsheet or pinging the pricing team.

---

## What it does
- Quotes four services: **PCA Debt, PCA Equity, ESA, and Zoning**.
- Accepts a free-form description, a guided Q&A, or a fill-in template.
- Returns the rule-based total, the ML comparison, the starting base fee, and a breakdown of which pricing factors applied (turnaround, primary property type, size, location, travel, site complexity, prior reports, etc.).
- Answers "what-if" questions instantly (e.g., "what if the turnaround were 5 days?").

## How it works (the tools, end to end)
| Layer | Tool | Role |
|-------|------|------|
| **Interface** | Copilot Studio agent in **Microsoft Teams** | Natural-language chat where users request quotes |
| **Bridge** | Power Platform **Custom Connector** | Securely connects Teams to our pricing API |
| **Brains** | **Flask API** on Keboola | Runs both pricing methods on the same inputs, returns one combined answer |
| → | Rule-based engine | Canonical pricing algorithm (ported from the production logic) |
| → | ML model | Gradient-boosted model trained on quote data, for comparison |
| **Data** | Keboola **pricing factors** table | Production pricing coefficients driving the rule-based engine |
| **Self-serve UI** | **Streamlit calculator** (web link) | Browser-based version of the same engine for ad-hoc use |

**Flow:** User asks in Teams → agent collects the details → calls the connector → API runs both engines → agent replies with both estimates and a plain-English breakdown.

## Why it matters
- **Speed:** quotes in seconds, in a tool people already use (Teams).
- **Consistency:** every estimate uses the same canonical algorithm — no manual spreadsheet drift.
- **Transparency:** shows exactly which factors drove the price.
- **Two views:** rule-based (source of record) plus an ML sanity-check.

## Status
- ✅ Live and working in Teams: quoting, breakdowns, what-ifs, guided entry, starter prompts.
- ✅ Streamlit calculator live for browser use.
- ✅ Smart cold-start handling so the first request of the day stays smooth.
- 💲 Runs on a small Keboola backend that sleeps when idle — pennies-to-a-few-dollars per month.

## Roadmap
- Train the ML model on **real historical quotes** (currently a placeholder for testing).
- Optional **in-chat form** (Adaptive Card) for one-click structured entry.
- Broaden services / factors as pricing rules evolve.

---
*Internal tool — Partner Engineering and Science.*
