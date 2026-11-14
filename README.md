# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of a selected country**.

## Data sources (fetched live, cached 24h, with a Refresh button)
| Input | Source |
|---|---|
| U.S. industry unlevered betas | Aswath Damodaran, `betas.xls` (Unlevered beta column) |
| Baa2/BBB spread, large non-financial firms | Aswath Damodaran, `ratings.html` |
| Combined statutory corporate tax rates | OECD Corporate Tax Statistics (via OECD data API, dataset `DSD_TAX_CIT@DF_CIT`) |
| 10-year government bond yields | Trading Economics, `/bonds` (scraped, no API key) |

Each input shows its source, data date and fetch status. Any fetched value can be replaced with a manual input; if a fetch fails, the manual input switches on automatically.

## Method
- Levered beta: βL = βU × [1 + (1 − t) × D/E], with D/E = d / (1 − d)
- USD cost of equity: rE$ = rf$ + βL × ERP
- USD cost of debt: rD$ = rf$ + USD BBB spread
- WACC = (1 − d) × rE + d × rD × (1 − t)
- Foreign currency (interest parity): (1 + r_FC) = (1 + r$) × (1 + rf_FC) / (1 + rf$)
- FC BBB spread = rD_FC − rf_FC (auto), or a manual override that affects only FC results

The same project tax rate (U.S. or selected country, switchable), debt share and ERP are used in both currencies.

## Defaults
Software (System & Application) · United States · U.S. tax rate · 6.5% ERP · 30% debt

## Features
- Side-by-side USD vs foreign-currency results
- All-industry comparison table
- CSV downloads (results and all-industry table)
- Built-in verification checks (equal yields, zero debt, FC spread override)

## Run locally
```
pip install -r requirements.txt
streamlit run app.py
```
