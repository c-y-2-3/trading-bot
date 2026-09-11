# Trading Bot — Upgrades Brief (v2: exits, reliability, evaluation)

## Context
v1 works end-to-end in dry-run: data + news + Claude decision + risk checks + logging, and news now feeds real headlines into the reasoning. Before enabling **live paper orders**, we are hardening the bot — especially **exit/stop-loss logic** and **reliability for unattended multi-day running** — and adding a way to **measure** whether it's any good.

All v1 hard constraints still apply: **paper only**, secrets in `.env`, **risk enforced in code**, **dry-run remains the default**, and **stop at each review milestone** below.

## Build order
Implement **Tier 1 fully and STOP for review** before starting Tier 2. Same for Tier 2 before Tier 3.

---

## TIER 1 — Safety & correctness (required before any live paper order)

### 1. Broker-side protective stops (most important)
The bot runs once per day, but positions are exposed 24/5. Protection must live **at the broker**, executing automatically even while the bot and PC are off — not inside the bot's sleep cycle.
- On every buy, attach a protective exit at Alpaca. **Default: a trailing-stop sell** (ratchets up with price, locks in gains — good for momentum). Support a **bracket order** (entry + fixed stop-loss + take-profit, OCO) as an alternative.
- Start with a flat config default `TRAILING_STOP_PCT = 0.08`; this becomes volatility-aware in Tier 2.
- Config flags: `USE_TRAILING_STOP = True`, `TRAILING_STOP_PCT`.
- Verify current `alpaca-py` signatures for trailing-stop / bracket orders and confirm they work in the **paper** environment.

### 2. Position reconciliation (broker is the source of truth)
At the **start of every cycle**, fetch actual positions and open orders from Alpaca and treat them as ground truth — a stop may have fired overnight.
- Build the account snapshot and "current positions" passed to Claude from Alpaca's live data, never from local assumptions.
- Detect and log any position closed by a stop since the previous run.

### 3. Review-and-exit logic for existing holdings
Claude must explicitly reconsider open positions each cycle and be able to **SELL**, not just buy/hold.
- The prompt must list current holdings with entry price, current price, unrealised P/L, and that symbol's latest news, and instruct Claude to judge whether each thesis still holds (e.g. exit on broken thesis or clearly negative news).
- Make the **sell path** work end-to-end through `risk.py` + `trader.py` (currently untested): a sell reduces/closes a position and **never goes short** in v1 (net quantity floored at 0).

### 4. Fail-safe error handling + data validation
A bot left alone for weeks must fail safe, never crash mid-loop, never trade on bad data.
- Wrap Alpaca and Anthropic calls in retry + backoff; on persistent failure, **skip the cycle cleanly** (no orders, log the error) instead of crashing.
- Per-symbol validation: if price data is missing, stale, or absurd (price <= 0, or a 1-day move beyond a sane threshold suggesting a glitch), force that symbol to `hold` and log why.
- Validate model output beyond schema: reject symbols not in the watchlist, negative quantities, and sells exceeding held quantity.

### 5. Idempotency + market-calendar awareness
- Use Alpaca's clock/calendar API: only act on a valid trading day; if the market is closed either skip or queue market-on-open (keep it simple); never run on weekends/holidays.
- Guard against double-trading: if the bot already ran/traded for the current trading day (crash-restart or scheduler double-fire), detect it (a per-day marker, or by checking today's orders) and do not repeat buys.

**STOP after Tier 1 and report:** show a dry-run where the prompt includes current holdings with P/L, the model produces at least one **sell** decision in a constructed test, and confirm a trailing stop would be attached on a buy. Review before Tier 2.

---

## TIER 2 — Decision quality

### 6. Confidence-weighted sizing + confidence floor
- Scale position size by the model's `confidence` instead of always ~max: target position % = `MAX_POSITION_PCT * confidence`, then apply existing caps.
- Skip any buy with `confidence < CONFIDENCE_FLOOR` (config default `0.55`).

### 7. ATR-based (volatility-aware) stops
- Compute a simple ~14-day ATR per symbol from the bars already fetched.
- Set each position's trailing-stop distance from ATR (e.g. `2 * ATR` as a %), so calm stocks get tight stops and volatile ones get room; fall back to flat `TRAILING_STOP_PCT` if ATR is unavailable.
- Optionally include ATR/volatility in the prompt as context.

### 8. Benchmark tracking (the scorecard)
- Each cycle, log total equity **and** the value of an equal-money buy-and-hold of SPY since the bot started.
- Add `report.py` to print/plot the bot's equity curve vs the SPY benchmark from the logs.

**STOP after Tier 2 and review.**

---

## TIER 3 — Operability for unattended running (nice-to-have)
- **Notifications:** on each trade and on any error/skipped cycle, send a short alert (Discord or Telegram webhook is simplest; email via SMTP also fine).
- **Daily summary log:** one line per day — equity, day P/L, positions, vs-benchmark.
- Keep the existing `STOP` kill-switch.

---

## Evaluation note (put in README)
- The honest test is **forward paper trading vs a buy-and-hold SPY benchmark**, watched over weeks.
- Do **not** rely on a historical backtest: the model already knows how past events resolved (lookahead/hindsight bias) and point-in-time historical news is hard to reproduce. Forward paper testing is the clean measure.

---

Still **paper-only**. **Dry-run remains the default.** Do not enable `--once` live order placement until Tier 1 is built, reviewed, and explicitly approved.
