# Trading Bot — Architecture Reference

Paper trading bot that asks Claude for daily trade decisions across a 25-symbol watchlist,
applies hard-coded risk controls, and places Alpaca paper orders with broker-side protective
stops. All secrets live in `.env`; `paper=True` is hardcoded and never negotiable.

---

## 1. File Tree

```
config.py                   All tunable constants — watchlist, risk limits, ATR params, model name
main.py                     Entry point; orchestrates the full cycle; benchmark tracking; scheduler
logger.py                   Logging setup; JSONL log writer; console summary printer
notify.py                   Discord webhook — fire-and-forget summary and alert sender
report.py                   CLI report: equity curve vs SPY benchmark from JSONL logs
risk.py                     Every hard risk check (kill switch, daily halt, sizing caps, short guard)

data/
  prices.py                 Alpaca bar fetch (IEX); ATR computation per symbol; account snapshot
  news.py                   Alpaca news fetch with per-symbol retry and 3-day lookback

brain/
  prompt.py                 Builds the structured user prompt from account/price/news data
  decide.py                 Calls Claude (tool_use), validates response, returns parsed dict

execution/
  trader.py                 Submits OTO (entry + stop) or plain market orders to Alpaca
  stops.py                  ATR trail helpers; attach_trailing_stop(); reconcile_stops()

scenarios/
  test_sell_path.py         Test: inject AMZN at $290 entry, expect SELL recommendation
  test_confidence_floor.py  Test: empty-cash account, verify low-confidence buys are rejected

logs/                       Runtime output (gitignored)
  decisions.jsonl           One JSONL record per live/dry-run cycle
  test_decisions.jsonl      One JSONL record per test scenario run

.benchmark_state.json       Persisted SPY share count for benchmark tracking (gitignored)
STOP                        Kill-switch file — create to block all orders (gitignored)
.env                        API keys (gitignored, never committed)
.env.example                Template showing required env var names
requirements.txt            Pinned dependencies
```

---

## 2. The Decision Cycle — End to End

This traces a single `python main.py --once` invocation from process start to exit.

**Step 0 — Process start**
`main()` in `main.py` calls `load_dotenv()` and `setup_logging()`, then parses CLI flags.
`setup_logging()` forces UTF-8 on Windows stdout/stderr (without this, Unicode in model
responses crashes on cp1252 consoles). `_require_env()` aborts early if any of the three
required keys are missing.

**Step 1 — Guard: is it a trading day?**
`_is_trading_day()` calls `TradingClient.get_calendar(GetCalendarRequest(start=today, end=today))`.
If the list is empty (weekend or holiday), it sends a Discord skip alert and returns — no
further work is done. On calendar API failure, it assumes a trading day and continues.

**Step 2 — Guard: idempotency (live mode only)**
In `--once` without `--dry-run`, `_already_ran_today()` fetches today's orders from Alpaca
and looks for any market BUY orders placed since UTC midnight. If found, it sends a Discord
skip alert and returns. This prevents double-trading if the process is restarted mid-day.
Dry-run mode skips this check entirely — you can run it multiple times safely.

**Step 3 — Account snapshot**
`get_account_snapshot()` calls Alpaca for the account object and all open positions,
returning a normalised dict with `equity`, `cash`, `buying_power`, `last_equity`, and a
`positions` list. Every field comes directly from Alpaca — the bot never constructs its
own position state.

**Step 4 — Closed-position detection**
`_load_last_positions()` reads the tail of `logs/decisions.jsonl` (last 16 KB) to get the
position list from the previous run. `_detect_closed_positions()` diffs that against the
current Alpaca positions and logs any that disappeared — meaning a broker-side trailing
stop fired overnight.

**Step 5 — Price bars and ATR**
`get_price_stats()` fetches 30+ days of daily bars for all watchlist symbols via
`StockHistoricalDataClient` using `feed=DataFeed.IEX` (required for free paper accounts;
the SIP feed requires a paid subscription). For each symbol it computes:
- `current_price` (last close), `change_1d_pct`, `change_5d_pct`, `change_30d_pct`
- `atr_14` — 14-period Average True Range in dollars
  `TR = max(H−L, |H−prev_C|, |L−prev_C|)`, averaged over last 14 bars
- `atr_trail_pct` — `2 × ATR / price × 100`, clamped to [3%, 15%]

Symbols with missing data, zero/negative prices, or a 1-day move exceeding 25% are
silently dropped from the stats dict. Any symbol absent from `price_stats` is forced to
HOLD by the prompt and by `risk.py`.

**Step 6 — Stop reconciliation**
Before new orders, `reconcile_stops()` fetches all open orders for currently-held positions
and identifies any that lack a sell-side stop (`TRAILING_STOP`, `STOP`, or `STOP_LIMIT`).
For each unprotected position it submits a standalone GTC trailing-stop sell at the
ATR-computed `atr_trail_pct`. This is the safety net for positions acquired via OPG orders
(which cannot carry an OTO stop leg) or any edge case where the OTO leg was missed.

**Step 7 — News**
`get_news()` calls Alpaca's `NewsClient` once per watchlist symbol. A `start` date of
"3 days ago" is required — without it the API returns nothing (a discovered quirk). Up to
5 articles per symbol are returned. Symbols with no articles get an empty list; the model
is told "No recent news." CCJ and ITA frequently return zero articles; the bot handles
this gracefully.

**Step 8 — Benchmark**
`.benchmark_state.json` is loaded. If it does not exist (first run), the bot computes how
many SPY shares `initial_equity / spy_price` would have bought and writes the file. Every
subsequent cycle multiplies that fixed share count by today's SPY price to get the
benchmark's current value, then computes `bot_equity / benchmark_value − 1` as alpha.

**Step 9 — Build the prompt**
`build_prompt()` assembles a structured Markdown string that contains:
1. Risk limits (max position %, confidence floor, max invested %, halt threshold, stop info)
2. Account snapshot (equity, cash, buying power)
3. Open positions with entry price, current price, unrealised P/L, and ATR stop distance —
   with explicit instructions to SELL or HOLD each one with a stated reason
4. Watchlist data: for every symbol, price changes across 1d/5d/30d, ATR trail %, and up
   to 5 recent headlines with summaries
5. Instructions: call `trade_decision` for every symbol; set confidence honestly; cite
   specific data in reasoning

**Step 10 — Claude API call**
`get_trade_decision()` in `brain/decide.py` calls `client.messages.create()` with:
- `model`: `claude-sonnet-4-6` (from `config.MODEL`)
- `tool_choice={"type": "tool", "name": "trade_decision"}` — forces the model to respond
  via tool_use rather than prose; eliminates regex parsing of free text
- `max_tokens=4096`
- The tool schema (see Section 3)

If the response contains no `tool_use` block (which should not happen given `tool_choice`
but is defended against), the model is re-prompted once with an explicit instruction. Two
consecutive failures raise `RuntimeError`, which is caught by the outer try/except.

**Step 11 — Post-schema validation**
`_validate_decisions()` applies checks the JSON schema cannot enforce:
- Drops any symbol not in `config.WATCHLIST`
- Clamps negative quantities to 0 (converts to hold)
- Converts sells on symbols not currently held to holds
- Caps sell quantity at held quantity (with a warning log)

**Step 12 — Risk checks**
`apply_risk_checks()` in `risk.py` processes each decision in watchlist order, maintaining
a running total of invested capital and buying power. HOLDs always pass through unchanged.
The full filter chain for buys is described in Section 4.

**Step 13 — Order placement**
`place_orders()` in `execution/trader.py` checks `clock.is_open`. If the market is open:
- BUYs are submitted as `OrderClass.OTO` (one-triggers-other) with a `StopLossRequest`
  whose `stop_price` = `current_price × (1 − atr_trail_pct / 100)`. This is a single
  atomic Alpaca request — the stop leg activates automatically when the buy fills, with
  no race condition.
- SELLs are plain `MarketOrderRequest(time_in_force=DAY)`.

If the market is closed (clock check failed — fallback only): plain
`MarketOrderRequest(time_in_force=OPG)` for both buys and sells; stop attachment deferred
to Step 6 of the next cycle.

In dry-run mode: all of the above is logged but nothing is submitted.

**Step 14 — Logging and notification**
`log_cycle()` in `logger.py` assembles a single JSON record and appends it to
`logs/decisions.jsonl`. It also prints a human-readable summary to stdout. The function
returns the record dict, which `run_cycle()` passes to `notify.send_summary()`. If any
exception was raised during the cycle it was already caught and stored in `errors[]`, so
the record and summary always write — even on partial failure.

**Step 15 — Process exits**
`main.py` returns. The terminal session can be closed. No state is held in memory.

**Next cycle — what happens to open positions**
At Step 6 of the following cycle, `reconcile_stops()` fetches all open orders for
current positions and verifies each has a stop. Any OTO stop that fired overnight (causing
the position to close) will simply not appear in the current position list — it is logged
as a closed position at Step 4. Any position that somehow lost its stop gets a new
trailing stop attached immediately, before the model is consulted.

---

## 3. The Decision Schema

The model is constrained to call exactly one tool, `trade_decision`, whose input is:

```json
{
  "decisions": [
    {
      "symbol":     "NVDA",
      "action":     "buy",
      "quantity":   27,
      "order_type": "market",
      "reasoning":  "NVDA supply chain positive — Coherent Texas expansion...",
      "confidence": 0.72
    }
  ],
  "portfolio_comment": "Deploying selectively into AI infrastructure..."
}
```

**Field-by-field consumption:**

| Field | Type | Consumed by | Notes |
|---|---|---|---|
| `symbol` | string | `_validate_decisions`, `risk.py`, `trader.py` | Dropped if not in `WATCHLIST` |
| `action` | `"buy"/"sell"/"hold"` | `risk.py`, `trader.py` | Sells on unheld positions converted to hold |
| `quantity` | integer ≥ 0 | `risk.py` then `trader.py` | Trimmed by confidence cap, position cap, invested cap, buying power; must be 0 for hold |
| `order_type` | `"market"` | Not used in routing | Constrained to `"market"` in schema; execution always uses market orders |
| `reasoning` | string | `logger.py` (logged), `notify.py` (not included) | Cited in console summary; stored in JSONL |
| `confidence` | float 0.0–1.0 | `risk.py` | Buys below 0.55 rejected outright; position size = `MAX_POSITION_PCT × confidence` |
| `portfolio_comment` | string | `logger.py` (logged) | Console summary only; not used in execution |

The `order_type` field exists in the schema but is redundant — execution always submits
market orders. It was included to encourage the model to think about order type explicitly
and could be removed without changing behaviour.

---

## 4. Risk Controls

All controls in `risk.py` run **after** the model's proposal and **before** any order
reaches Alpaca. Controls in `data/prices.py` and `brain/decide.py` run **before** the
model even sees the data.

### Pre-model controls

**Data validation** (`data/prices.py`, before prompt)
- Per-symbol: if bars are missing, price ≤ 0, or 1-day move > 25%, the symbol is dropped
  from `price_stats`. The prompt tells the model `[DATA UNAVAILABLE — must HOLD]` for
  dropped symbols. Protects against data glitches being traded on.

**Output validation** (`brain/decide.py`, before risk.py)
- Off-watchlist symbols dropped
- Negative quantities clamped to 0
- Sells on unheld positions converted to hold
- Sell quantity capped at held quantity
These run before `risk.py` to give cleaner input to the risk engine.

### Post-model controls (all in `risk.py`)

**Kill switch** — `risk.py` checks for a file named `STOP` in the project root. If it
exists, all non-hold decisions are blocked for that cycle. Create this file to pause
trading immediately without stopping the process. Runs first, before any other check.

**Daily loss halt** — if `(equity − last_equity) / last_equity < −0.05` (5%), all BUY
decisions are rejected for the cycle. HOLDs and SELLs are unaffected. Protects against
compounding losses on a bad day.

**Confidence floor** — buys with `confidence < 0.55` are rejected. The floor is set
in `config.CONFIDENCE_FLOOR`. The model self-reports confidence; the floor prevents the
bot from deploying capital on hesitant signals.

**Confidence-weighted sizing** — approved buys are sized to
`floor(MAX_POSITION_PCT × confidence × equity / price)` shares. A 65%-confidence signal
gets at most 6.5% of equity, not the full 10%. The model may propose more; the excess
is trimmed with a log line.

**Single-position cap** — after confidence sizing, the resulting position value
(existing market value + order value) is checked against `MAX_POSITION_PCT × equity`
(10%). If it would breach the cap, only as many shares as fit are bought.

**Total-invested cap** — a running total of deployed capital tracks all buys in the
current cycle. If adding this order would push total invested above
`MAX_TOTAL_INVESTED_PCT × equity` (80%), the order is trimmed to fit or rejected.

**Buying-power check** — the order value is checked against remaining buying power. If
insufficient, trimmed to what fits; if nothing fits, rejected.

**Short-sale prevention** — sell quantity is capped at the held quantity. The bot never
takes a net-short position. Enforced in both `_validate_decisions` and `risk.py`.

### Broker-side controls (execution layer)

**OTO stop-loss** (`execution/trader.py`, placed with each live buy)
- For in-hours buys: the OTO order's stop leg sets a fixed stop price at
  `current_price × (1 − atr_trail_pct / 100)`. Activates atomically when the parent
  buy fills — no race condition. The stop is a fixed price (not trailing), because
  Alpaca does not support trailing stops as OTO/bracket exit legs.
- Note: this is a fixed stop, not a ratcheting one. A position that gains 10% still
  has its stop at the original ATR distance below the entry price.

**Trailing-stop reconciliation** (`execution/stops.py`, start of every cycle)
- Checks every open position for an existing sell stop. Attaches a standalone GTC
  trailing-stop sell for any that are unprotected. This covers: OPG orders (which
  can't carry an OTO leg), stops that were cancelled manually, and any edge cases.
- Recognises `OrderType.TRAILING_STOP`, `STOP`, and `STOP_LIMIT` as "protected" so
  it does not double-attach to OTO positions.

**ATR stop distance** (`execution/stops.py` and `data/prices.py`)
- `atr_trail_pct = clamp(2 × ATR_14 / price × 100, 3%, 15%)`
- Low-volatility symbols (e.g. SPY) get tight stops (≈3%). High-volatility names
  (e.g. OKLO, SMR) hit the 15% ceiling. The flat fallback `TRAILING_STOP_PCT = 8%`
  applies only when bar data is unavailable.

**Market calendar check** (`main.py`, before cycle body)
- `GetCalendarRequest(start=today, end=today)` returns an empty list on weekends and
  holidays. Cycle skips entirely with a Discord alert.

**Idempotency guard** (`main.py`, before cycle body, live mode only)
- Fetches today's orders; if any market BUY already exists, the cycle aborts. Prevents
  double-trading from manual re-runs or scheduler double-fires.

---

## 5. Run Modes

```
python main.py --once --dry-run    Fetch data, call Claude, log decisions — NO orders
python main.py --once              Fetch data, call Claude, place real paper orders
python main.py --loop --dry-run    APScheduler daemon, 09:35 ET weekdays, dry-run only
python main.py --loop              APScheduler daemon, 09:35 ET weekdays, live orders
python report.py                   Print equity vs SPY table (live cycles only)
python report.py --all             Include dry-run cycles in report
python report.py --tests           Print test scenario table from test_decisions.jsonl
python scenarios/test_sell_path.py     Run sell-path scenario (writes test log)
python scenarios/test_confidence_floor.py  Run confidence-floor scenario
```

**What `--dry-run` actually prevents:**
- `place_orders()` logs intended orders and returns `[]` without calling Alpaca
- `reconcile_stops()` logs intended stop attachments without submitting
- `_already_ran_today()` is skipped entirely (safe to run multiple times)

**What `--dry-run` does NOT prevent:**
- Fetching account data, prices, and news from Alpaca
- Calling the Anthropic API (Claude is consulted even in dry-run)
- Writing to `logs/decisions.jsonl`
- Sending a Discord summary

**The `--loop` scheduler:**
Uses `APScheduler.BlockingScheduler` with a `cron` trigger at 09:35 ET, Monday–Friday.
The process blocks indefinitely; Ctrl+C stops it cleanly. The scheduler fires `run_cycle()`
which itself checks the market calendar — so a scheduler fire on a holiday (calendar API
says closed) simply skips without trading.

**Environment variables (all required unless noted):**
```
ALPACA_API_KEY          Alpaca paper account key
ALPACA_SECRET_KEY       Alpaca paper account secret
ANTHROPIC_API_KEY       Anthropic API key for Claude
DISCORD_WEBHOOK_URL     (optional) Discord webhook URL — absent = notifications silently skipped
```

---

## 6. External Touchpoints

### Alpaca — `TradingClient` (paper=True)
| Call | Purpose |
|---|---|
| `get_calendar(GetCalendarRequest)` | Market calendar check — is today a trading day? |
| `get_orders(GetOrdersRequest)` | Idempotency guard (today's buys) + stop reconciliation (open sell stops) |
| `get_account()` | Equity, cash, buying power, last equity |
| `get_all_positions()` | Live position list (source of truth) |
| `get_clock()` | Is market currently open? (gates OTO vs OPG order type) |
| `submit_order(MarketOrderRequest, OTO)` | Live buy + atomic stop-loss leg |
| `submit_order(MarketOrderRequest)` | Live sell or OPG fallback buy |
| `submit_order(TrailingStopOrderRequest)` | Stop reconciliation — standalone trailing stop |

### Alpaca — `StockHistoricalDataClient`
| Call | Purpose |
|---|---|
| `get_stock_bars(StockBarsRequest, feed=IEX)` | 30+ days daily OHLCV bars for all watchlist symbols |

IEX feed is required. The SIP feed (`feed=DataFeed.SIP`) is rejected with a subscription
error on free paper accounts.

### Alpaca — `NewsClient`
| Call | Purpose |
|---|---|
| `get_news(NewsRequest(symbols, start, limit, sort))` | Up to 5 articles per symbol, last 3 days |

Called once per symbol (25 sequential calls). A `start` parameter is mandatory — the API
returns nothing without it.

### Anthropic API
| Call | Purpose |
|---|---|
| `client.messages.create(model, tools, tool_choice, messages)` | One call per cycle; `tool_choice={"type":"tool","name":"trade_decision"}` forces structured JSON output via tool_use |

Model: `claude-sonnet-4-6`. Max tokens: 4096. No streaming.
The API is called even in dry-run mode.

### Discord webhook
`notify.py` POSTs to `DISCORD_WEBHOOK_URL` via stdlib `urllib.request` (no extra
dependency). Two message types:
- `send_alert(title, body)` — immediate; sent on cycle errors and skipped cycles
- `send_summary(record)` — sent after every completed cycle (dry-run or live)

All calls catch every exception and log a warning — the bot never crashes because Discord
is unreachable. The `User-Agent: trading-bot/1.0` header is required; the default Python
urllib agent is blocked by Cloudflare in front of Discord.

---

## 7. Design Notes

**Paper-only by construction, not by flag.**
`paper=True` is hardcoded in every `TradingClient` call. There is no CLI flag or env var
that switches to the live endpoint. Moving to live trading requires editing source code,
which is intentional friction.

**Risk is enforced in Python, never trusted to the model.**
The model proposes; `risk.py` disposes. This is explicitly documented in `CLAUDE.md` as
an operating rule. The model's confidence score is an input to the sizing formula, not
a bypass of it.

**OTO orders instead of post-fill stop attachment.**
The original implementation attached a trailing stop immediately after placing a market
buy. Alpaca rejects stop-sells while the parent buy is `PENDING_NEW` ("cannot open a
short sell while a long buy order is open"). The fix is an OTO (one-triggers-other) order,
which is a single atomic Alpaca request where the stop leg only activates after the buy
fills. This is why the stop is a fixed price (not trailing) — Alpaca's `StopLossRequest`
(the only supported OTO exit leg) takes a `stop_price`, not a `trail_percent`. The
trailing-stop behaviour lives in `reconcile_stops()` for cases the OTO path cannot cover.

**ATR-based stop distances instead of a flat percentage.**
A flat stop (e.g. 8% for everything) is either too tight for volatile stocks (triggering
on noise) or too loose for calm ones (allowing large drawdowns). ATR scales the stop
to each symbol's actual volatility. SPY's ATR gives a ≈3% stop; NVDA/AMD sit around 8–14%.
The 3–15% clamp prevents degenerate values on data outliers.

**Confidence-weighted position sizing.**
Without this, the model would implicitly request max allocation for any signal it chose
to act on, since the risk caps define only an upper bound. Scaling size by confidence
means a 60%-confident signal gets 60% of the maximum allocation, not 100%. This is the
difference between "I think this will probably work" and "I have high conviction here."

**`tool_choice` forces structured output.**
Using `tool_choice={"type": "tool", "name": "trade_decision"}` means the model *must*
call that tool and cannot respond with prose. This eliminates all regex-based response
parsing and schema validation becomes the only parsing logic needed.

**Two log files for auditability.**
`logs/decisions.jsonl` contains only real cycle runs (live and dry-run). Test scenarios
(which inject fake account states to verify specific behaviours) write to
`logs/test_decisions.jsonl`. This prevents test data from polluting the performance
record that `report.py` reads. Both files are gitignored.

**SPY benchmark as the honest scorecard.**
The benchmark is computed as "how would an investor who put everything into SPY on the
same start date be doing?" This is the right comparison for a trading strategy: not
absolute returns, not a risk-free rate, but the passive alternative the bot has to beat.
The benchmark state is persisted in `.benchmark_state.json` so it survives process
restarts without resetting.

**Kill switch by filesystem sentinel.**
Creating a file named `STOP` in the project root causes `risk.py` to block all orders
that cycle, without stopping the process or modifying any code. This is intentional:
the bot continues to run, fetch data, call Claude, and log decisions — it just does not
trade. Useful for pausing during market events without losing the decision audit trail.
The file is gitignored.

**Retry + backoff on all external calls.**
Both `data/prices.py` and `data/news.py` wrap Alpaca calls in `_retry()`: up to 3
attempts with 2s → 4s exponential backoff. Per-symbol news failures produce empty lists
rather than aborting the cycle. A persistent price-bar failure returns an empty dict,
which causes all symbols to be forced-hold for the cycle rather than crashing.

**One ambiguity to be aware of:**
`execution/trader.py` imports `attach_trailing_stop` from `execution/stops.py` but does
not call it in the current `place_orders()` implementation. The function is used only
inside `reconcile_stops()` in `stops.py`. The import in `trader.py` is dead code — a
leftover from the pre-OTO implementation. It does not affect behaviour.
