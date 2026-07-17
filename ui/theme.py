"""
ui/theme.py
Presentation-layer only: CSS injection for the redesigned dashboard shell
(dark/light/system theme, glassmorphism cards, gradients, animated KPI
counters). Touches zero business logic, state_store, or engine code --
purely st.markdown(unsafe_allow_html=True) styling plus a small amount of
CSS-driven JS for the animated number counters.
"""
import streamlit as st

THEME_KEY = "ui_theme"
DEFAULT_THEME = "system"  # "system" | "dark" | "light"


def get_theme():
    return st.session_state.get(THEME_KEY, DEFAULT_THEME)


def set_theme(theme):
    st.session_state[THEME_KEY] = theme


def _theme_data_attr():
    theme = get_theme()
    if theme == "system":
        return ""  # let prefers-color-scheme decide, no forced override
    return f'data-theme="{theme}"'


def inject_global_css():
    """Call once per page render, before any other UI. Sets CSS variables
    for both color schemes and applies them app-wide via a wrapper div."""
    theme_attr = _theme_data_attr()
    st.markdown(
        f"""
        <style>
        :root {{
            --accent: #6366f1;
            --accent-2: #22d3ee;
            --profit: #22c55e;
            --loss: #ef4444;
            --warn: #f59e0b;
        }}
        /* ---- Dark (default) ---- */
        [data-theme="dark"], :root:not([data-theme]) {{
            --bg: #0b0e14;
            --bg-elevated: #12161f;
            --card-bg: rgba(255,255,255,0.04);
            --card-border: rgba(255,255,255,0.08);
            --text: #e6e9ef;
            --text-dim: #8b93a7;
        }}
        @media (prefers-color-scheme: light) {{
            :root:not([data-theme]) {{
                --bg: #f5f7fa;
                --bg-elevated: #ffffff;
                --card-bg: rgba(15,23,42,0.03);
                --card-border: rgba(15,23,42,0.08);
                --text: #0f172a;
                --text-dim: #5b6478;
            }}
        }}
        [data-theme="light"] {{
            --bg: #f5f7fa;
            --bg-elevated: #ffffff;
            --card-bg: rgba(15,23,42,0.03);
            --card-border: rgba(15,23,42,0.08);
            --text: #0f172a;
            --text-dim: #5b6478;
        }}

        .stApp {{
            background: var(--bg);
            color: var(--text);
        }}
        section[data-testid="stSidebar"] {{
            background: var(--bg-elevated);
            border-right: 1px solid var(--card-border);
        }}

        /* ---- Glass card ---- */
        .glass-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: 18px 20px;
            backdrop-filter: blur(10px);
            box-shadow: 0 4px 24px rgba(0,0,0,0.18);
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}
        .glass-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.28);
        }}

        /* ---- KPI card ---- */
        .kpi-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: 16px 18px;
            backdrop-filter: blur(10px);
        }}
        .kpi-label {{
            font-size: 0.78rem;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}
        .kpi-value {{
            font-size: 1.6rem;
            font-weight: 700;
            font-variant-numeric: tabular-nums;
        }}
        .kpi-sub {{
            font-size: 0.8rem;
            color: var(--text-dim);
            margin-top: 4px;
        }}
        .kpi-value.profit {{ color: var(--profit); }}
        .kpi-value.loss {{ color: var(--loss); }}

        /* ---- Gradient accent bar (page headers) ---- */
        .grad-header {{
            background: linear-gradient(90deg, var(--accent), var(--accent-2));
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            font-weight: 800;
        }}

        /* ---- Ticker ---- */
        .ticker-wrap {{
            display: flex;
            gap: 14px;
            overflow-x: auto;
            padding: 10px 4px;
            margin-bottom: 4px;
        }}
        .ticker-item {{
            flex: 0 0 auto;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 8px 16px;
            display: flex;
            flex-direction: column;
            min-width: 150px;
        }}
        .ticker-name {{ font-size: 0.75rem; color: var(--text-dim); }}
        .ticker-price {{ font-size: 1.05rem; font-weight: 700; font-variant-numeric: tabular-nums; }}
        .ticker-change.profit {{ color: var(--profit); font-size: 0.8rem; }}
        .ticker-change.loss {{ color: var(--loss); font-size: 0.8rem; }}

        /* ---- AI signal card ---- */
        .signal-card {{
            background: linear-gradient(135deg, rgba(99,102,241,0.10), rgba(34,211,238,0.06));
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 20px;
        }}
        .signal-badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.03em;
        }}
        .signal-badge.ce {{ background: rgba(34,197,94,0.18); color: var(--profit); }}
        .signal-badge.pe {{ background: rgba(239,68,68,0.18); color: var(--loss); }}
        .confidence-bar-bg {{
            width: 100%;
            height: 6px;
            border-radius: 999px;
            background: var(--card-border);
            margin-top: 6px;
        }}
        .confidence-bar-fill {{
            height: 6px;
            border-radius: 999px;
            background: linear-gradient(90deg, var(--accent), var(--accent-2));
        }}

        /* ---- Status pill ---- */
        .status-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 600;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
        }}
        .status-dot {{
            width: 8px; height: 8px; border-radius: 50%;
        }}
        .status-dot.on {{ background: var(--profit); box-shadow: 0 0 8px var(--profit); }}
        .status-dot.off {{ background: var(--text-dim); }}
        .status-dot.warn {{ background: var(--warn); box-shadow: 0 0 8px var(--warn); }}

        /* ---- Timeline ---- */
        .timeline-item {{
            display: flex;
            gap: 12px;
            padding: 8px 0;
            border-bottom: 1px solid var(--card-border);
        }}
        .timeline-item:last-child {{ border-bottom: none; }}
        .timeline-time {{ color: var(--text-dim); font-size: 0.78rem; min-width: 72px; }}
        .timeline-tag {{
            font-size: 0.72rem;
            font-weight: 700;
            padding: 1px 8px;
            border-radius: 6px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            height: fit-content;
        }}

        div[data-testid="stDataFrame"] {{
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--card-border);
        }}
        </style>
        <script>
        var htmlEl = window.parent.document.documentElement;
        {f'htmlEl.setAttribute("data-theme", "{get_theme()}");' if theme_attr else 'htmlEl.removeAttribute("data-theme");'}
        </script>
        """,
        unsafe_allow_html=True,
    )


def theme_switcher():
    """Sidebar theme picker -- default System, plus Dark/Light overrides."""
    st.selectbox(
        "Theme", ["system", "dark", "light"],
        index=["system", "dark", "light"].index(get_theme()),
        key="_theme_select",
        format_func=lambda t: t.capitalize(),
        on_change=lambda: set_theme(st.session_state["_theme_select"]),
    )
