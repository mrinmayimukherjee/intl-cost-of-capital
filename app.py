import io
import json
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="International Cost of Capital", layout="wide")
st.title("International Cost of Capital: USD vs Foreign Currency")

# ---------------- Sources ----------------
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
BETA_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
SPREAD_URLS = ["https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
               "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"]
TAX_PAGE = ("https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
            "full-report/statutory-corporate-income-tax-rates_440f9286.html")
TAX_API = ("https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
           "?startPeriod=2022&format=csvfilewithlabels")
TE_URL = "https://tradingeconomics.com/bonds"
DEFAULT_INDUSTRY = "Software (System & Application)"
MONTHS = r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d\d"

# Trading Economics name -> OECD name
ALIASES = {"US": "United States", "UK": "United Kingdom", "South Korea": "Korea",
           "Turkey": "Türkiye", "Czech Republic": "Czechia", "Slovakia": "Slovak Republic",
           "Russia": "Russian Federation", "China": "China (People's Republic of)",
           "Hong Kong": "Hong Kong, China", "Vietnam": "Viet Nam", "Taiwan": "Chinese Taipei"}


# ---------------- Data fetchers (cached for 1 day) ----------------
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_betas():
    r = requests.get(BETA_URL, headers=HDR, timeout=30)
    r.raise_for_status()
    sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None, header=None)
    for raw in sheets.values():
        first = raw.iloc[:, 0].astype(str).str.strip()
        hits = raw.index[first.eq("Industry Name")]
        if len(hits) == 0:
            continue
        h = hits[0]
        df = raw.iloc[h + 1:].copy()
        df.columns = [" ".join(str(c).split()) for c in raw.iloc[h]]  # collapse line breaks in headers
        ucol = next(c for c in df.columns if c.lower() == "unlevered beta")
        out = df[["Industry Name", ucol]].copy()
        out.columns = ["Industry", "Unlevered beta"]
        out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
        out = out.dropna()
        out = out[~out["Industry"].astype(str).str.contains("Total Market", case=False)]
        asof = None  # look for a "Date updated" cell above the table
        for _, row in raw.iloc[:h].iterrows():
            vals = [v for v in row.tolist() if pd.notna(v)]
            for i, v in enumerate(vals):
                if "date" in str(v).lower() and i + 1 < len(vals):
                    asof = str(vals[i + 1])[:10]
                    break
            if asof:
                break
        return out.reset_index(drop=True), asof or r.headers.get("Last-Modified", "n/a")
    raise ValueError("Could not find the 'Industry Name' table in betas.xls")


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_bbb_spread():
    err = None
    for url in SPREAD_URLS:
        try:
            r = requests.get(url, headers=HDR, timeout=30)
            r.raise_for_status()
            # First table on the page = large non-financial service firms
            for t in pd.read_html(io.StringIO(r.text)):
                cells = [["" if pd.isna(v) else str(v) for v in row] for row in t.values.tolist()]
                hits = [row for row in cells if any("Baa2/BBB" in v for v in row)]
                if not hits:
                    continue
                row = hits[0]
                nums = [float(x) for v in row for x in re.findall(r"-?\d+\.?\d*", v.replace("%", ""))]
                val = nums[-1]
                val = val / 100 if val > 0.2 else val  # "1.11%" -> 0.0111
                m = re.search(MONTHS, r.text)
                return val, (m.group(0) if m else r.headers.get("Last-Modified", "n/a"))
        except Exception as e:
            err = e
    raise RuntimeError(f"Baa2/BBB row not found ({err})")


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_tax_rates():
    r = requests.get(TAX_API, headers=HDR, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    # Combined (central + sub-central) rate = MEASURE "CIT_C"; standard statutory rate = TARGETING "ST"
    d = df[(df["MEASURE"] == "CIT_C") & (df["TARGETING"] == "ST")].copy()
    d["OBS_VALUE"] = pd.to_numeric(d["OBS_VALUE"], errors="coerce")
    d["yr"] = pd.to_numeric(d["TIME_PERIOD"], errors="coerce")
    d = d.dropna(subset=["OBS_VALUE", "yr"])
    name = "Reference area" if "Reference area" in d.columns else "REF_AREA"
    d = d[d["yr"] == d.groupby(name)["yr"].transform("max")]          # latest year per country
    out = d.groupby(name).agg(rate=("OBS_VALUE", "max"), year=("yr", "max")).reset_index()
    out.columns = ["Country", "Tax rate", "Year"]
    out["Tax rate"] = out["Tax rate"] / 100
    out["Year"] = out["Year"].astype(int)
    return out


# OECD name -> Trading Economics URL slug (others: lowercase, spaces -> hyphens)
TE_SLUGS = {"Korea": "south-korea", "Türkiye": "turkey", "Czechia": "czech-republic",
            "Slovak Republic": "slovakia", "China (People's Republic of)": "china",
            "Hong Kong, China": "hong-kong", "Viet Nam": "vietnam", "Chinese Taipei": "taiwan",
            "Russian Federation": "russia"}


def te_url(country):
    slug = TE_SLUGS.get(country) or re.sub(r"[^a-z0-9]+", "-", country.lower()).strip("-")
    return f"https://tradingeconomics.com/{slug}/government-bond-yield"


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_yield(country):
    """Read the 10y yield embedded in the country's public bond page (TEChartsMeta)."""
    url = te_url(country)
    r = requests.get(url, headers=HDR, timeout=30)
    waf = r.headers.get("x-amzn-waf-action", "")
    if waf or r.status_code != 200:
        raise ValueError(f"{country}: Trading Economics returned HTTP {r.status_code}"
                         f"{' (WAF ' + waf + ')' if waf else ''}. Enter the yield manually.")
    m = re.search(r"TEChartsMeta\s*=\s*(\[.*?\]);", r.text, re.S)
    if not m:
        raise ValueError(f"{country}: no yield data found on {url}. Enter the yield manually.")
    try:
        meta = json.loads(m.group(1))[0]
        value = float(meta["value"])
    except Exception:
        v = re.search(r'"value"\s*:\s*(-?[0-9.]+)', m.group(1))
        if not v:
            raise ValueError(f"{country}: could not read the yield value. Enter it manually.")
        meta, value = {}, float(v.group(1))
    date = next((str(meta[k]) for k in ("last_update", "lastUpdate", "date", "LastUpdate") if meta.get(k)),
                datetime.now(timezone.utc).strftime("%Y-%m-%d (fetched)"))
    return value / 100, date, meta.get("symbol", "")


def safe(fn):
    try:
        return fn(), None
    except Exception as e:
        return None, str(e)


def lookup_tax(taxes, country):
    if taxes is None:
        return None, None
    for n in (country, ALIASES.get(country, country)):
        hit = taxes[taxes["Country"].str.lower() == n.lower()]
        if len(hit):
            return float(hit["Tax rate"].iloc[0]), int(hit["Year"].iloc[0])
    hit = taxes[taxes["Country"].str.contains(country, case=False, regex=False)]
    return (float(hit["Tax rate"].iloc[0]), int(hit["Year"].iloc[0])) if len(hit) else (None, None)


# ---------------- Core calculation ----------------
def cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us, spread_fc_override=None):
    de = d / (1 - d)
    beta_l = beta_u * (1 + (1 - tax) * de)
    re_us = rf_us + beta_l * erp
    rd_us = rf_us + spread_us
    wacc_us = (1 - d) * re_us + d * rd_us * (1 - tax)
    k = (1 + rf_fc) / (1 + rf_us)                 # interest-parity conversion factor
    re_fc = (1 + re_us) * k - 1
    rd_fc = (1 + rd_us) * k - 1 if spread_fc_override is None else rf_fc + spread_fc_override
    wacc_fc = (1 - d) * re_fc + d * rd_fc * (1 - tax)
    return {
        "Risk-free rate (10y govt)": (rf_us, rf_fc),
        "Levered beta": (beta_l, beta_l),
        "Cost of equity": (re_us, re_fc),
        "BBB spread": (spread_us, rd_fc - rf_fc),
        "Pre-tax cost of debt": (rd_us, rd_fc),
        "After-tax cost of debt": (rd_us * (1 - tax), rd_fc * (1 - tax)),
        "WACC": (wacc_us, wacc_fc),
    }


# ---------------- Load data ----------------
if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()
with st.spinner("Fetching latest data..."):
    betas_res, betas_err = safe(fetch_betas)
    spread_res, spread_err = safe(fetch_bbb_spread)
    taxes, tax_err = safe(fetch_tax_rates)
fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
betas, beta_date = betas_res if betas_res else (None, None)
spread_auto, spread_date = spread_res if spread_res else (None, None)

# ---------------- Sidebar inputs ----------------
st.sidebar.header("Project settings")
industries = list(betas["Industry"]) if betas is not None else [DEFAULT_INDUSTRY]
industry = st.sidebar.selectbox("Industry", industries,
                                index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
countries = sorted(taxes["Country"]) if taxes is not None else ["United States", "Germany", "Japan",
                                                                  "United Kingdom", "Colombia", "Brazil", "India"]
if "United States" not in countries:
    countries = ["United States"] + countries
country = st.sidebar.selectbox("Target country", countries, index=countries.index("United States"))
erp = st.sidebar.slider("Equity risk premium (%)", 3.0, 10.0, 6.5, 0.1) / 100
d = st.sidebar.slider("Target debt share (%)", 0, 90, 30) / 100
tax_choice = st.sidebar.radio("Project tax rate", ["U.S.", country if country != "United States" else "Selected country"],
                              horizontal=True)

# Fetched values
beta_f = float(betas.loc[betas["Industry"] == industry, "Unlevered beta"].iloc[0]) if betas is not None else None
tax_us_f, tax_us_yr = lookup_tax(taxes, "United States")
tax_fc_f, tax_fc_yr = lookup_tax(taxes, country)


def y_of(c):
    res, err = safe(lambda: fetch_yield(c))
    return (res[0], res[1], err) if res else (None, None, err)


rf_us_f, rf_us_date, yld_err_us = y_of("United States")
rf_fc_f, rf_fc_date, yld_err_fc = y_of(country) if country != "United States" else (rf_us_f, rf_us_date, yld_err_us)
yld_err = "; ".join(dict.fromkeys(e for e in (yld_err_us, yld_err_fc) if e)) or None

st.sidebar.header("Manual overrides")


def param(label, fetched, key, default):
    """Use the fetched value unless the user ticks Override (forced on if fetch failed)."""
    forced = fetched is None
    on = st.sidebar.checkbox(f"Override {label}", value=forced, key=f"ov_{key}", disabled=forced)
    start = fetched if fetched is not None else default
    if on:
        return st.sidebar.number_input(f"{label} (%)" if key != "beta" else label,
                                       value=round(start * (1 if key == "beta" else 100), 4),
                                       step=0.01, key=f"in_{key}") / (1 if key == "beta" else 100), True
    shown = f"{fetched:.2f}" if key == "beta" else f"{fetched:.2%}"
    st.sidebar.caption(f"{label}: **{shown}** (fetched)")
    return fetched, False


beta_u, ov_beta = param("Unlevered beta", beta_f, "beta", 1.0)
tax_us, ov_tus = param("U.S. tax rate", tax_us_f, "tax_us", 0.25)
if country == "United States":
    tax_fc, ov_tfc = tax_us, ov_tus
else:
    tax_fc, ov_tfc = param(f"{country} tax rate", tax_fc_f, "tax_fc", 0.25)
spread_us, ov_spr = param("USD BBB spread", spread_auto, "spread", 0.011)
rf_us, ov_rus = param("U.S. 10y yield", rf_us_f, "rf_us", 0.04)
if country == "United States":
    rf_fc, ov_rfc = rf_us, ov_rus
else:
    rf_fc, ov_rfc = param(f"{country} 10y yield", rf_fc_f, "rf_fc", 0.04)

tax = tax_us if tax_choice == "U.S." else tax_fc

# Foreign-currency spread: auto unless switched on
auto_fc_spread = cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us)["BBB spread"][1]
fc_override_on = st.sidebar.toggle("Override foreign-currency BBB spread")
if fc_override_on:
    spread_fc_override = st.sidebar.number_input("FC BBB spread (%)", value=round(auto_fc_spread * 100, 4),
                                                 step=0.01) / 100
else:
    spread_fc_override = None
    st.sidebar.caption(f"FC BBB spread (auto): **{auto_fc_spread:.2%}**")

# ---------------- Results ----------------
res = cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us, spread_fc_override)
fc_label = f"{country} currency"
df = pd.DataFrame(res, index=["USD", fc_label]).T


def fmt(row):
    return row.map(lambda x: f"{x:.2f}") if row.name == "Levered beta" else row.map(lambda x: f"{x:.2%}")


c1, c2, c3 = st.columns(3)
c1.metric("WACC (USD)", f"{res['WACC'][0]:.2%}")
c2.metric(f"WACC ({fc_label})", f"{res['WACC'][1]:.2%}")
c3.metric("Project tax rate", f"{tax:.1%}", tax_choice)

st.subheader(f"Results side by side — {industry}, {country}")
st.table(df.apply(fmt, axis=1))

# ---------------- Sources ----------------
st.subheader("Data sources")
def status(err, ov):
    if err and ov:
        return "Fetch failed - manual input"
    return "Manual override" if ov else "Fetched"


src = pd.DataFrame([
    ["Unlevered beta (Damodaran, U.S. industries)", status(betas_err, ov_beta), beta_date, BETA_URL],
    ["Baa2/BBB spread, large non-financial firms (Damodaran)", status(spread_err, ov_spr), spread_date, SPREAD_URLS[0]],
    ["Combined statutory CIT rate, U.S. (OECD)", status(tax_err or (tax_us_f is None), ov_tus), tax_us_yr, TAX_PAGE],
    [f"Combined statutory CIT rate, {country} (OECD)", status(tax_err or (tax_fc_f is None), ov_tfc), tax_fc_yr, TAX_PAGE],
    ["U.S. 10y government yield (Trading Economics)", status(yld_err_us or (rf_us_f is None), ov_rus), rf_us_date, te_url("United States")],
    [f"{country} 10y government yield (Trading Economics)", status(yld_err_fc or (rf_fc_f is None), ov_rfc), rf_fc_date, te_url(country)],
], columns=["Input", "Status", "Data as of", "Source"])
src["Data as of"] = src["Data as of"].map(lambda v: "n/a" if v is None or (isinstance(v, float) and v != v) else str(v))
st.dataframe(src[["Input", "Status", "Data as of", "Source"]], hide_index=True, width="stretch",
             column_config={"Source": st.column_config.LinkColumn()})
st.caption(f"Last fetch: {fetched_at}. OECD rates pulled via the OECD data API (dataset DSD_TAX_CIT@DF_CIT).")
errs = {k: v for k, v in {"Betas": betas_err, "Spread": spread_err, "Tax": tax_err, "Yields": yld_err}.items() if v}
if errs:
    with st.expander("⚠️ Fetch errors (use manual overrides)"):
        for k, v in errs.items():
            st.write(f"**{k}:** {v}")

# ---------------- Assumptions ----------------
with st.expander("Method and key assumptions", expanded=False):
    st.markdown(f"""
- **Levered beta:** βL = βU × [1 + (1 − t) × D/E], with D/E = d / (1 − d).
- **USD cost of equity:** rE$ = rf$ + βL × ERP. **USD cost of debt:** rD$ = rf$ + BBB spread.
- **WACC:** (1 − d) × rE + d × rD × (1 − t), using the same t, d and ERP in both currencies.
- **Foreign currency:** converted by interest parity, (1 + r_FC) = (1 + r$) × (1 + rf_FC) / (1 + rf$).
  The FC BBB spread is rD_FC − rf_FC unless overridden; an override changes only the FC side.
- 10-year government yields proxy the risk-free rate in each currency (no default-spread adjustment).
- Betas are U.S. industry unlevered betas (Damodaran); ERP is a user input (default 6.5%).
- Tax rate: combined central + sub-central statutory rate (OECD), latest year available.
""")

# ---------------- All-industry comparison ----------------
st.subheader("All-industry comparison")
if betas is not None:
    rows = []
    for _, b in betas.iterrows():
        r_ = cost_of_capital(b["Unlevered beta"], tax, d, erp, rf_us, rf_fc, spread_us, spread_fc_override)
        rows.append({"Industry": b["Industry"], "Unlevered beta": b["Unlevered beta"],
                     "Levered beta": r_["Levered beta"][0],
                     "Cost of equity (USD)": r_["Cost of equity"][0], f"Cost of equity ({fc_label})": r_["Cost of equity"][1],
                     "WACC (USD)": r_["WACC"][0], f"WACC ({fc_label})": r_["WACC"][1]})
    all_ind = pd.DataFrame(rows).sort_values("WACC (USD)", ascending=False).reset_index(drop=True)
    pct_cols = [c for c in all_ind.columns if c.startswith(("Cost", "WACC"))]
    st.dataframe(all_ind.style.format({**{c: "{:.2%}" for c in pct_cols},
                                       "Unlevered beta": "{:.2f}", "Levered beta": "{:.2f}"})
                 .apply(lambda r: ["background-color: #fff3cd" if r["Industry"] == industry else "" for _ in r], axis=1),
                 hide_index=True, width="stretch", height=400)
else:
    all_ind = None
    st.info("Industry betas could not be fetched, so the comparison is unavailable.")

# ---------------- Downloads ----------------
inputs = pd.DataFrame({"Input": ["Industry", "Country", "ERP", "Debt share", "Project tax rate (choice)",
                                 "Tax rate used", "Unlevered beta", "USD BBB spread", "U.S. 10y yield",
                                 f"{country} 10y yield", "FC spread override"],
                       "Value": [industry, country, erp, d, tax_choice, tax, beta_u, spread_us, rf_us, rf_fc,
                                 spread_fc_override if spread_fc_override is not None else "off (auto)"]})
out = pd.concat([inputs, pd.DataFrame({"Input": df.index, "Value": df["USD"].values, "FC": df[fc_label].values})])
d1, d2 = st.columns(2)
d1.download_button("⬇️ Download results (CSV)", out.to_csv(index=False), "cost_of_capital_results.csv", "text/csv")
if all_ind is not None:
    d2.download_button("⬇️ Download all-industry table (CSV)", all_ind.to_csv(index=False),
                       "all_industry_comparison.csv", "text/csv")

# ---------------- Built-in verification checks ----------------
with st.expander("Verification checks"):
    a = cost_of_capital(beta_u, tax, d, erp, rf_us, rf_us, spread_us)
    eq = all(abs(v[0] - v[1]) < 1e-12 for v in a.values())
    z = cost_of_capital(beta_u, tax, 0.0, erp, rf_us, rf_fc, spread_us)
    zero = abs(z["WACC"][0] - z["Cost of equity"][0]) < 1e-12 and abs(z["WACC"][1] - z["Cost of equity"][1]) < 1e-12
    o1 = cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us, None)
    o2 = cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us, 0.05)
    usd_same = all(abs(o1[k][0] - o2[k][0]) < 1e-12 for k in o1)
    st.write(f"{'✅' if eq else '❌'} Equal government yields → identical USD and FC results")
    st.write(f"{'✅' if zero else '❌'} Zero debt → WACC equals cost of equity (both currencies)")
    st.write(f"{'✅' if usd_same else '❌'} Overriding FC spread leaves USD results unchanged")
