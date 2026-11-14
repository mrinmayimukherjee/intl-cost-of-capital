import streamlit as st
import pandas as pd

st.set_page_config(page_title="International Cost of Capital", layout="wide")
st.title("International Cost of Capital: USD vs Foreign Currency")

# ---------- Core calculation ----------
def cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us, spread_fc_override=None):
    de = d / (1 - d)                                  # D/E from debt share
    beta_l = beta_u * (1 + (1 - tax) * de)            # re-levered beta
    re_us = rf_us + beta_l * erp                      # USD cost of equity
    rd_us = rf_us + spread_us                         # USD cost of debt
    wacc_us = (1 - d) * re_us + d * rd_us * (1 - tax)

    k = (1 + rf_fc) / (1 + rf_us)                     # interest-parity conversion factor
    re_fc = (1 + re_us) * k - 1
    if spread_fc_override is None:
        rd_fc = (1 + rd_us) * k - 1                   # auto: convert USD debt cost
    else:
        rd_fc = rf_fc + spread_fc_override            # manual FC spread
    spread_fc = rd_fc - rf_fc
    wacc_fc = (1 - d) * re_fc + d * rd_fc * (1 - tax)

    return {
        "Risk-free rate (10y govt)": (rf_us, rf_fc),
        "Levered beta": (beta_l, beta_l),
        "Cost of equity": (re_us, re_fc),
        "BBB spread": (spread_us, spread_fc),
        "Pre-tax cost of debt": (rd_us, rd_fc),
        "After-tax cost of debt": (rd_us * (1 - tax), rd_fc * (1 - tax)),
        "WACC": (wacc_us, wacc_fc),
    }

# ---------- Inputs (manual for Stage 1; placeholders) ----------
st.sidebar.header("Inputs")
country = st.sidebar.text_input("Target country", "United States")
erp = st.sidebar.number_input("Equity risk premium (%)", value=6.5, step=0.1) / 100
d = st.sidebar.slider("Target debt share (%)", 0, 90, 30) / 100

beta_u = st.sidebar.number_input("Unlevered beta", value=1.20, step=0.01)
tax_us = st.sidebar.number_input("U.S. corporate tax rate (%)", value=25.8) / 100
tax_fc = st.sidebar.number_input(f"{country} corporate tax rate (%)", value=25.8) / 100
tax_choice = st.sidebar.radio("Project tax rate", ["U.S.", "Selected country"])
tax = tax_us if tax_choice == "U.S." else tax_fc

rf_us = st.sidebar.number_input("U.S. 10y yield (%)", value=4.10, step=0.01) / 100
rf_fc = st.sidebar.number_input(f"{country} 10y yield (%)", value=4.10, step=0.01) / 100
spread_us = st.sidebar.number_input("USD BBB spread (%)", value=1.10, step=0.01) / 100

override = st.sidebar.toggle("Override foreign-currency BBB spread")
spread_fc_override = (st.sidebar.number_input("FC BBB spread (%)", value=1.10, step=0.01) / 100
                      if override else None)

# ---------- Results ----------
res = cost_of_capital(beta_u, tax, d, erp, rf_us, rf_fc, spread_us, spread_fc_override)
df = pd.DataFrame(res, index=["USD", f"{country} currency"]).T

def fmt(row):
    return row.map(lambda x: f"{x:.2f}") if row.name == "Levered beta" else row.map(lambda x: f"{x:.2%}")

st.subheader("Results side by side")
st.table(df.apply(fmt, axis=1))
st.caption(f"Project tax rate used: {tax:.1%} ({tax_choice})")
