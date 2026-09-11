# Trading Bot — Build Brief (v1)

## What we're building
A **paper-trading** bot for **US stocks/ETFs**. Once per trading day it:
1. Gathers recent price data and news for a small watchlist,
2. Asks Claude (via the Anthropic API) for a structured trade decision,
3. Applies **hard-coded risk checks** to that decision,
4. Places paper orders through Alpaca,
5. Logs every decision and its full reasoning for later review.

This is v1: paper money only, conservative, auditable. **Optimise for clarity and safety over cleverness.**

---

## Tech stack
- Python 3.11
- `alpaca-py` (the current official Alpaca SDK — do NOT use the deprecated `alpaca-trade-api`), pointed at the **paper** environment
- `anthropic` (official Python SDK)
- `python-dotenv` for secrets
- `APScheduler` for the daily schedule (loop mode only)
- Standard library `logging`, writing JSON lines

If unsure about exact current SDK call signatures, check the latest Alpaca/Anthropic docs rather than guessing.

---

## Hard constraints (these must always hold)
1. **Paper trading only.** Use Alpaca's paper endpoint (`paper=True`). Never the live endpoint in v1.
2. **Secrets live in `.env`**, loaded via dotenv. Never hard-code keys. Create a `.gitignore` excluding `.env`, `logs/`, `__pycache__/`, and `STOP`.
3. **Risk limits are enforced in Python code, never trusted to the model.** The model *proposes*; the code *disposes*.
4. **Default run mode is dry-run** (compute + log decisions, place NO orders). Placing real paper orders requires an explicit flag.
5. **Build and test dry-run first.** Do not implement live order placement until dry-run works end to end.

---

## Project structure
```
claudecode/
├── .env                  # real keys (gitignored)
├── .env.example          # template, no real keys
├── .gitignore
├── requirements.txt
├── README.md
├── CLAUDE.md             # operating rules for future Claude Code sessions
├── config.py             # watchlist, risk params, model name
├── main.py               # orchestrates everything; argparse run modes
├── logger.py             # structured JSONL logging
├── risk.py               # position sizing, loss cap, kill switch
├── data/
│   ├── prices.py         # price history + account state from Alpaca
│   └── news.py           # recent news from Alpaca
├── brain/
│   ├── prompt.py         # builds the decision prompt
│   └── decide.py         # calls Anthropic API, parses + validates JSON
├── execution/
│   └── trader.py         # places paper orders for risk-approved decisions
└── logs/
    └── decisions.jsonl   # appended each cycle (gitignored)
```

---

## Configuration (`config.py`) — these are defaults the user can edit
```python
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "SPY", "QQQ"]
MODEL = "claude-sonnet-4-6"
MAX_POSITION_PCT = 0.10        # max 10% of equity in any single symbol
MAX_TOTAL_INVESTED_PCT = 0.80  # always keep >= 20% in cash
DAILY_LOSS_HALT_PCT = 0.05     # if equity down >5% vs start of day, halt new buys
PRICE_LOOKBACK_DAYS = 30
MAX_NEWS_PER_SYMBOL = 5
```

---

## Data inputs
**`data/prices.py`**
- For each watchlist symbol: fetch ~30 daily bars; derive current price and % change over 1d / 5d / 30d.
- Fetch account state: equity, cash, buying power, and current positions (symbol, qty, avg entry price, unrealised P/L).

**`data/news.py`**
- For each symbol: fetch up to `MAX_NEWS_PER_SYMBOL` most recent news items (headline, short summary, timestamp, source) from Alpaca's news endpoint.

---

## The decision call (the core of the bot)
Assemble a single prompt to Claude containing:
- The risk limits in plain language (so the model proposes within them),
- An account snapshot (equity, cash, current positions with unrealised P/L),
- Per symbol: the price stats + recent headlines/summaries,
- An instruction to return **JSON only**, no prose outside it.

**System prompt:** a concise instruction that it is a disciplined, risk-aware portfolio assistant operating on **paper money** with a **multi-day horizon**, that it must stay within the stated risk limits, and that it must respond with **valid JSON only**.

**Required output schema** (the model returns exactly this shape):
```json
{
  "decisions": [
    {
      "symbol": "AAPL",
      "action": "buy",
      "quantity": 5,
      "order_type": "market",
      "reasoning": "1-3 sentences citing specific price action and/or a named news item.",
      "confidence": 0.62
    }
  ],
  "portfolio_comment": "1-2 sentence overall view of positioning."
}
```
- `action` is one of `buy` | `sell` | `hold`; `quantity` is an integer (0 for hold); `confidence` is 0.0-1.0.
- **Parsing:** extract JSON robustly (strip any markdown fences), validate the shape. On parse failure, retry once with a stricter reminder; if it still fails, skip the cycle and log the failure. Using the Anthropic SDK's tool-use / structured-output feature to force valid JSON is preferred if straightforward.

---

## Risk enforcement (`risk.py`) — applied to every proposed decision before any order
- Trim or reject buys that would push a symbol above `MAX_POSITION_PCT` of equity.
- Reject buys that would push total invested above `MAX_TOTAL_INVESTED_PCT`.
- Reject buys when cash / buying power is insufficient.
- Reject sells of a symbol not held, or for more than the held quantity.
- If equity is down more than `DAILY_LOSS_HALT_PCT` since the start of the trading day, block all new buys (sells and holds still allowed).
- **Kill switch:** if a file named `STOP` exists in the project root, place no orders this cycle.
- Log every adjustment/rejection with its reason.

---

## Execution (`execution/trader.py`)
- Runs only when NOT in dry-run.
- Places market orders via Alpaca paper for the risk-approved decisions.
- If the US market is closed, submit orders as market-on-open (use an appropriate `time_in_force`) rather than failing — keep this simple.

---

## Logging (`logger.py`)
Append one JSON object per cycle to `logs/decisions.jsonl`:
- timestamp, run mode, account snapshot before,
- the model's raw decision, the risk adjustments applied, the orders actually placed, any errors.
Also print a clean human-readable summary to the console each cycle.

---

## Run modes (`main.py`, via argparse)
- `python main.py --once --dry-run`  → **default**; one cycle, fetch + decide + log, place NO orders
- `python main.py --once`            → one real paper cycle
- `python main.py --loop`            → schedule one cycle per trading day (APScheduler), real paper orders

Print a startup banner showing: mode, paper/live, watchlist, and the active risk limits.

---

## Also create
- `requirements.txt` (reasonable pinned versions)
- `.env.example` (keys named but blank)
- `.gitignore`
- `README.md`: how to add keys to `.env`, and how to run each mode
- `CLAUDE.md`: short standing rules — paper only, dry-run is default, risk enforced in code, never commit `.env`, always confirm before enabling order placement

---

## STOP HERE for review
Your first milestone is to get **`python main.py --once --dry-run`** working end to end: it fetches data, gets a valid JSON decision from Claude, runs it through the risk checks, and logs everything — while placing **no orders**.

**Do not enable real paper order placement yet.** Once dry-run produces a clean decision + log, stop and report back so the output can be reviewed before going further.
