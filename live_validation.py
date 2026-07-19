"""
live_validation.py
Pure functions that turn "live paper trading has been running for N weeks"
into an actual statistical comparison against the strategy's backtest
expectation, instead of just eyeballing a P&L number -- this is the
evidence a go/no-go real-money decision should be based on (see
GO_NO_GO_CHECKLIST.md). No network/DB access here except the one CSV/DB
read each helper does; everything else is a plain function over data the
caller passes in, so it's unit-testable without a live engine running.
"""
import math
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKTEST_EXPERIMENTS_CSV = os.path.join(BASE_DIR, "backtest_experiments_results.csv")

MIN_TRADES_FOR_SIGNIFICANCE = 30  # normal-approximation binomial test needs a reasonable sample


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def binomial_win_rate_test(wins, total, expected_p=0.5):
    """Two-sided test of whether the observed win rate differs significantly
    from expected_p, via a normal approximation with continuity correction
    (no scipy dependency in this repo -- accurate enough once total is a
    few dozen or more; see MIN_TRADES_FOR_SIGNIFICANCE).

    Returns None if total == 0. Otherwise a dict with observed_win_rate,
    z, p_value, and significant (p_value < 0.05) -- 'significant' here
    means "win rate is reliably NOT expected_p," not "the strategy is
    good," so callers should combine it with the actual win rate and P&L
    sign, not read it alone.
    """
    if total == 0:
        return None
    observed_p = wins / total
    se = math.sqrt(expected_p * (1 - expected_p) / total)
    if se == 0:
        return {"observed_win_rate": observed_p, "z": None, "p_value": None, "significant": False}
    cc = 0.5 / total
    diff = max(abs(observed_p - expected_p) - cc, 0)
    z = diff / se
    p_value = 2 * (1 - _norm_cdf(z))
    return {
        "observed_win_rate": round(observed_p * 100, 1),
        "z": round(z, 2),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
    }


def summarize_live_trades(closed_positions):
    """closed_positions: list of dicts from state_store.get_closed_positions()
    (each has net_pnl, exit_time, ...). Returns None if empty."""
    if not closed_positions:
        return None
    df = pd.DataFrame(closed_positions)
    total = len(df)
    wins = int((df["net_pnl"] > 0).sum())
    cum = df.sort_values("exit_time")["net_pnl"].cumsum()
    running_max = cum.cummax()
    return {
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate_pct": round(wins / total * 100, 1),
        "total_pnl": round(df["net_pnl"].sum(), 2),
        "avg_pnl": round(df["net_pnl"].mean(), 2),
        "max_drawdown": round((cum - running_max).min(), 2),
        "significance": binomial_win_rate_test(wins, total),
    }


def load_backtest_test_window_baseline(strategy_name):
    """Reads backtest_experiments_results.csv (produced by running
    `python backtest_experiments.py` -- local-only, gitignored like the
    other backtest artifacts) and returns the held-out TEST-window row for
    strategy_name, which is the fairer baseline to compare live paper
    trading against (the TRAIN window is what the strategy was picked
    using, so it's optimistic by construction). Returns None if the file
    or a matching row doesn't exist."""
    if not os.path.exists(BACKTEST_EXPERIMENTS_CSV):
        return None
    df = pd.read_csv(BACKTEST_EXPERIMENTS_CSV)
    test_rows = df[df["label"].str.startswith(f"{strategy_name} [TEST")]
    if test_rows.empty:
        return None
    row = test_rows.iloc[0]
    return {
        "trades": int(row["trades"]),
        "win_rate_pct": float(row["win_rate"]),
        "total_pnl": float(row["total_pnl"]),
        "avg_pnl": float(row["avg_pnl"]),
    }


def compare_live_vs_backtest(strategy_name, closed_positions):
    """Top-level comparison the Validation page renders. Returns a dict with
    'live' (summarize_live_trades result or None), 'backtest_baseline'
    (load_backtest_test_window_baseline result or None), and 'verdict' --
    a short plain-English read, deliberately conservative: it only ever
    says live performance is "tracking" or "diverging," never "proven,"
    since that judgment belongs in GO_NO_GO_CHECKLIST.md's explicit gates,
    not a single number here.
    """
    live = summarize_live_trades(closed_positions)
    baseline = load_backtest_test_window_baseline(strategy_name)

    if live is None:
        return {"live": None, "backtest_baseline": baseline, "verdict": "No closed live trades yet."}

    if live["total_trades"] < MIN_TRADES_FOR_SIGNIFICANCE:
        verdict = (
            f"Only {live['total_trades']} live trades so far -- too few to draw any conclusion "
            f"(need {MIN_TRADES_FOR_SIGNIFICANCE}+ for the significance test to mean anything)."
        )
    elif baseline is None:
        verdict = (
            "No backtest_experiments_results.csv baseline found for this strategy -- "
            "run `python backtest_experiments.py` to generate one to compare against."
        )
    else:
        win_rate_gap = live["win_rate_pct"] - baseline["win_rate_pct"]
        pnl_sign_matches = (live["total_pnl"] >= 0) == (baseline["total_pnl"] >= 0)
        if abs(win_rate_gap) <= 10 and pnl_sign_matches:
            verdict = (
                f"Tracking the backtest baseline reasonably well (win rate {live['win_rate_pct']}% "
                f"live vs {baseline['win_rate_pct']}% backtest test-window, same P&L sign)."
            )
        else:
            verdict = (
                f"Diverging from the backtest baseline (win rate {live['win_rate_pct']}% live vs "
                f"{baseline['win_rate_pct']}% backtest test-window"
                f"{', P&L sign flipped' if not pnl_sign_matches else ''}) -- worth investigating "
                "before trusting the backtest numbers further."
            )

    return {"live": live, "backtest_baseline": baseline, "verdict": verdict}
