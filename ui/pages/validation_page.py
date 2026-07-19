"""
ui/pages/validation_page.py
The actual evidence a real-money go/no-go decision should be based on --
compares live paper trading against the backtest's held-out TEST-window
expectation for the CURRENTLY ACTIVE strategy, with a sample-size-aware
significance test instead of eyeballing a P&L number. See
GO_NO_GO_CHECKLIST.md for the concrete numeric gates this feeds into.

Read-only: this page only reads state_store and live_validation, same
pattern as every other page -- it never writes financial state.
"""
import streamlit as st

import live_validation
import state_store
import strategies
from ui import components

st.set_page_config(page_title="Validation | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

components.section_header(
    "Validation",
    "Is live paper trading actually tracking the backtest, or diverging from it? "
    "This is the evidence to base a real-money decision on -- not the raw P&L number alone.",
)

snapshot = state_store.get_dashboard_snapshot()
active_strategy = snapshot["active_strategy"]
active_label = strategies.STRATEGIES.get(active_strategy, {}).get("label", active_strategy)

st.markdown(f"**Strategy being validated:** {active_label}")
st.caption(
    "Only trades closed while THIS strategy was active are meaningful here -- if you've been "
    "switching strategies, older trades under a different rule set don't belong in this "
    "comparison. This page doesn't currently filter by which strategy was active per-trade "
    "(that isn't tracked on the positions table yet); treat the numbers below as roughly "
    "indicative until that's added, and prefer comparisons taken during a stretch where you "
    "know the strategy wasn't changed."
)

closed_positions = state_store.get_closed_positions(limit=100000)
result = live_validation.compare_live_vs_backtest(active_strategy, closed_positions)

st.divider()

live = result["live"]
baseline = result["backtest_baseline"]

if live is None:
    st.info("No closed live trades yet -- nothing to validate until at least one trade completes.")
else:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        components.kpi_card("Live trades", live["total_trades"])
    with c2:
        components.kpi_card("Live win rate", f"{live['win_rate_pct']}%", f"{live['wins']}W / {live['losses']}L")
    with c3:
        tone = "profit" if live["total_pnl"] >= 0 else "loss"
        components.kpi_card("Live total P&L", f"Rs {live['total_pnl']:,.2f}", tone=tone)
    with c4:
        components.kpi_card("Live max drawdown", f"Rs {live['max_drawdown']:,.2f}", tone="loss")

    sig = live["significance"]
    if sig and sig["p_value"] is not None:
        sig_tone = "profit" if sig["significant"] else None
        st.caption(
            f"Win rate significance vs. a coin-flip (50%): p-value {sig['p_value']} "
            f"({'statistically significant' if sig['significant'] else 'NOT statistically significant'} "
            f"at the usual 0.05 threshold). This tests whether the win rate is reliably different "
            f"from random, not whether the strategy is profitable -- read it alongside the P&L above, not alone."
        )

    st.divider()

    if baseline is None:
        st.warning(
            "No backtest_experiments_results.csv found -- run `python backtest_experiments.py` "
            "locally to generate a baseline to compare against."
        )
    else:
        st.markdown(f"**Backtest baseline** (held-out TEST window, {baseline['trades']} trades -- "
                    "the fairer comparison since this is data the strategy wasn't picked using):")
        b1, b2 = st.columns(2)
        with b1:
            components.kpi_card("Backtest win rate", f"{baseline['win_rate_pct']}%")
        with b2:
            tone = "profit" if baseline["total_pnl"] >= 0 else "loss"
            components.kpi_card("Backtest total P&L", f"Rs {baseline['total_pnl']:,.2f}", tone=tone)

    st.divider()
    st.subheader("Verdict")
    st.markdown(result["verdict"])

with st.expander("Why this matters before real money"):
    st.markdown(
        "- The backtest numbers elsewhere in this app come from **simulated** option premiums "
        "(Black-Scholes using realized volatility as a stand-in for implied vol) -- see "
        "backtest.py's caveat. Simulated premiums obey put-call parity exactly, which real "
        "premiums don't; real spread, skew, and liquidity effects are invisible to that model.\n"
        "- Live paper trading uses REAL option premiums fetched from Upstox, so this comparison "
        "is the actual test of whether the backtest's simulated edge survives contact with real "
        "market prices -- not just \"is the strategy making money.\"\n"
        "- A `p_value < 0.05` here means the win rate is reliably not 50/50 -- it does NOT mean "
        "the strategy is proven safe for real money. See **GO_NO_GO_CHECKLIST.md** in the repo "
        "root for the actual numeric gates that decision should be based on."
    )
