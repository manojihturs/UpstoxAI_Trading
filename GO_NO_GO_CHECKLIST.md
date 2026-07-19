# Go / No-Go Checklist — Real Money

This document exists so the decision to switch from paper to real money is
made against numbers decided **now**, not by feel in ~6 months when the
excitement (or the losses) might bias the judgment either way. Nothing in
this app moves real money on its own — every gate below is a manual check
before a human decides to build and enable a live-order module, which
doesn't exist yet (see [Section 5](#5-what-doesnt-exist-yet)).

Track progress against this file directly — check items off as they're met,
and note the date/number next to each when it happens.

## 1. Statistical evidence (Validation page)

- [ ] **At least 100 closed live-paper trades** under the strategy that will
      go live, ideally more — `live_validation.MIN_TRADES_FOR_SIGNIFICANCE`
      (30) is the floor for the significance test to mean anything at all,
      not a target. 100+ gives a meaningfully tighter estimate.
- [ ] **Win rate significance test (Validation page) shows `significant: true`**
      — i.e. the live win rate is reliably different from a coin flip, not
      just luck within noise.
- [ ] **Live P&L total is positive**, and its sign matches the backtest's
      held-out TEST-window sign (not the TRAIN window — TRAIN is what the
      strategy was picked using, so it's optimistic by construction).
- [ ] **Live win rate is within ~10 percentage points of the backtest TEST
      baseline.** A big gap either direction means the simulated-premium
      backtest and real market prices are telling different stories —
      investigate before trusting either number.
- [ ] **The Validation page verdict says "Tracking," not "Diverging."**

## 2. Time and regime coverage

- [ ] **The live-paper period spans at least 2-3 distinct market regimes** —
      not just one long trend. Check the date range against Nifty's own
      chart: did it include at least one sideways/choppy stretch and one
      genuine trending stretch? A strategy that only got tested in one
      regime hasn't been tested against the regime it's most likely to fail
      in next.
- [ ] **No single week or month accounts for the majority of the total
      P&L.** If one lucky week is carrying the whole result, that's not a
      strategy edge, that's variance — check the per-week/per-month P&L
      breakdown (Analytics page) before trusting the aggregate number.
- [ ] **Walk-forward re-validation has been re-run at least once** during
      the 6 months (`python backtest_experiments.py`, or a rolling-window
      variant if built) to confirm the strategy's edge hasn't decayed since
      the original comparison.

## 3. Risk-control behavior, observed live

- [ ] **The daily loss cap (₹2,000) has actually tripped at least once**
      during live-paper testing, and confirmed to correctly block new
      entries for the rest of that day without needing a manual fix.
- [ ] **The cumulative drawdown breaker (₹15,000 / 30% of stated capital)
      has been deliberately tested** — either it tripped naturally, or it
      was simulated (e.g. via a scripted losing streak) and confirmed to
      correctly halt new entries and require manual reset.
- [ ] **`MAX_CONSECUTIVE_LOSSES` (2) and `MAX_TRADES_PER_DAY` (6) have both
      been observed firing correctly** at least once, not just assumed to
      work because the code looks right.
- [ ] **No silent engine outage** longer than a few minutes during market
      hours went unnoticed during the test period — see the monitoring
      checklist in `MONITORING.md`.

## 4. Cost and execution realism

- [ ] **Re-run the backtest with 2-3x the current `SLIPPAGE_PCT`**
      (`config.py`) and confirm the strategy is still net profitable. Real
      OTM option fills, especially near open/close, can be worse than the
      flat slippage assumption currently modeled.
- [ ] **Brokerage/STT/GST/stamp-duty constants in `cost_model.py` have been
      checked against your actual broker's current published rates** —
      these are noted as approximate in the code and get revised
      periodically.
- [ ] **Live paper trading's actual round-trip cost per trade (visible on
      the trade history export) roughly matches what the backtest assumed**
      — a large gap means the cost model needs recalibrating before it's
      trusted to size stops correctly with real money on the line.

## 5. What doesn't exist yet

Real-money trading is not a config flag flip. Before any real order is ever
placed, at minimum:

- [ ] A **separate, explicitly reviewed live-order module** — today
      `engine.py` only ever simulates fills; it has no code path that calls
      Upstox's order-placement endpoints (`/v2/order`, `/v3/order`), and
      that should stay true until this checklist is otherwise complete.
- [ ] A **hard `LIVE_TRADING_ENABLED` flag, defaulting to `False`**, checked
      at the point of order placement, not just in the UI.
- [ ] A **small-capital pilot phase** — even after everything above is
      green, start with real capital far below the full ₹50,000 stated
      capital, for a few more weeks, before scaling up. Passing paper
      validation reduces risk; it doesn't eliminate the gap between
      simulated and real order execution (fills, rejections, network
      latency, actual bid/ask at the moment of a real order).
- [ ] A **position state reconciliation check** — once real orders exist,
      periodically cross-check `engine.py`'s internal position state
      against Upstox's actual order/position API and alert on any mismatch.
      A silent state-drift bug is a very different kind of expensive with
      real money than with paper money.

## How to use this file

Re-visit it monthly during the 6-month test, not just at the end. If
several items are clearly failing early (e.g. the strategy is diverging
hard from backtest after 100 trades), that's a signal to reconsider the
strategy choice now, not wait out the full 6 months on a losing bet.
