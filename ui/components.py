"""
ui/components.py
Reusable presentation-only widgets for the redesigned dashboard. Every
function here takes plain data in and renders HTML/Streamlit widgets out --
none of them read state_store, call engine.py, or make any decisions.
Business logic and data fetching stay entirely in ui/pages/*.py and the
existing backend modules.
"""
import streamlit as st


def kpi_card(label, value, sub=None, tone=None):
    """tone: None | 'profit' | 'loss' -- colors the value only."""
    tone_class = f" {tone}" if tone in ("profit", "loss") else ""
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value{tone_class}">{value}</div>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_pill(label, state):
    """state: 'on' | 'off' | 'warn' -- drives the dot color."""
    st.markdown(
        f'<span class="status-pill"><span class="status-dot {state}"></span>{label}</span>',
        unsafe_allow_html=True,
    )


def section_header(title, subtitle=None):
    sub_html = f'<div class="kpi-sub" style="margin-bottom:12px;">{subtitle}</div>' if subtitle else ""
    st.markdown(f'<h3 class="grad-header">{title}</h3>{sub_html}', unsafe_allow_html=True)


def ticker_bar(items):
    """items: list of {name, price, change_pct} for the live market ticker."""
    cells = []
    for it in items:
        tone = "profit" if it["change_pct"] >= 0 else "loss"
        arrow = "▲" if it["change_pct"] >= 0 else "▼"
        cells.append(
            f"""<div class="ticker-item">
                <span class="ticker-name">{it['name']}</span>
                <span class="ticker-price">{it['price']:,.2f}</span>
                <span class="ticker-change {tone}">{arrow} {it['change_pct']:+.2f}%</span>
            </div>"""
        )
    st.markdown(f'<div class="ticker-wrap">{"".join(cells)}</div>', unsafe_allow_html=True)


def ai_signal_card(instrument, direction, entry, stop_loss, target, reasoning, confidence_pct=None):
    """direction: 'CE' | 'PE'. confidence_pct: 0-100, or None if the active
    strategy doesn't compute a probability score -- this app's strategies
    are rule-based (EMA/ADX/pivot/UT-Bot crossovers), not a trained
    classifier, so there is no real confidence number to show by default.
    Only pass confidence_pct if the caller has an actual score to report;
    never fabricate one just to fill the bar."""
    badge_class = "ce" if direction == "CE" else "pe"
    badge_label = "CALL (CE)" if direction == "CE" else "PUT (PE)"
    if confidence_pct is not None:
        confidence_html = f"""
            <div class="kpi-sub" style="margin-top:10px;">Confidence</div>
            <div class="confidence-bar-bg">
                <div class="confidence-bar-fill" style="width:{confidence_pct}%;"></div>
            </div>
            <div class="kpi-sub" style="margin-top:2px;">{confidence_pct:.0f}%</div>
        """
    else:
        confidence_html = (
            '<div class="kpi-sub" style="margin-top:10px;">Rule-based signal -- '
            'no ML confidence score</div>'
        )
    st.markdown(
        f"""
        <div class="signal-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div style="font-size:1.1rem; font-weight:700;">{instrument}</div>
                <span class="signal-badge {badge_class}">{badge_label}</span>
            </div>
            {confidence_html}
            <div style="display:flex; gap:24px; margin-top:14px;">
                <div><div class="kpi-label">Entry</div><div class="kpi-value" style="font-size:1.05rem;">{entry:,.2f}</div></div>
                <div><div class="kpi-label">Stop Loss</div><div class="kpi-value loss" style="font-size:1.05rem;">{stop_loss:,.2f}</div></div>
                <div><div class="kpi-label">Target</div><div class="kpi-value profit" style="font-size:1.05rem;">{target:,.2f}</div></div>
            </div>
            <div class="kpi-sub" style="margin-top:12px;">{reasoning}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def activity_timeline(events):
    """events: list of {timestamp, event_type, message}, most recent first."""
    rows = []
    for e in events:
        ts = e["timestamp"]
        time_part = ts.split("T")[1][:8] if "T" in ts else ts
        rows.append(
            f"""<div class="timeline-item">
                <div class="timeline-time">{time_part}</div>
                <div class="timeline-tag">{e['event_type']}</div>
                <div>{e['message']}</div>
            </div>"""
        )
    st.markdown(f'<div class="glass-card">{"".join(rows)}</div>', unsafe_allow_html=True)


def glass_card_start():
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)


def glass_card_end():
    st.markdown('</div>', unsafe_allow_html=True)
