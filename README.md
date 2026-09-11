# Paper Trading Bot

A daily paper-trading bot for US stocks/ETFs. Claude analyses price data and news, proposes trades, and risk checks gate every decision before any order is placed.

> **This is a proof of concept, not a real strategy.** It's an end-to-end exercise in wiring an LLM up to a broker safely — data in, a model decision, hard-coded risk checks, paper orders out, logging. The trading logic itself (what Claude is asked to weigh, the watchlist, position sizing) is intentionally undeveloped: the watchlist is just a handful of tickers picked for variety, and the decision-making was largely left to Claude to improvise rather than being a researched strategy. Don't expect it to be profitable, and don't point it at real money.

## Setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

2. **Add API keys to `.env`** (copy `.env.example` first)
   ```
   ALPACA_API_KEY=your_alpaca_paper_key
   ALPACA_SECRET_KEY=your_alpaca_paper_secret
   ANTHROPIC_API_KEY=your_anthropic_key
   ```
   Alpaca paper keys are available at https://app.alpaca.markets (switch to Paper in the dashboard).

## Run modes

| Command | What it does |
|---|---|
| `python main.py --once --dry-run` | Fetch data, get Claude's decision, run risk checks, **log everything — no orders placed** |
| `python main.py --once` | One full paper-trading cycle (places real paper orders) |
| `python main.py --loop` | Runs `--once` at 9:35 AM ET every weekday via APScheduler |
| `python report.py` | Print equity vs. SPY benchmark from the logs |

**Start with `--once --dry-run` and review `logs/decisions.jsonl` before enabling order placement.**

## Emergency stop

Create a file named `STOP` in the project root. The bot checks for it before placing any order and will skip the cycle if found.

## Risk limits (edit `config.py`)

- Max 10% of equity in any single symbol
- Max 80% of equity total invested
- Halt new buys if equity drops >5% intraday vs previous close
- Buys below a confidence floor are rejected outright; position size scales with confidence
- Every buy gets an ATR-based protective stop at the broker

Risk checks always run in Python (`risk.py`), never inside the model's response — see `ARCHITECTURE.md` for the full decision cycle, schema, and design rationale.

## Evaluation

There's no backtest here on purpose: the model already knows how past events played out, so a backtest would be trivially optimistic. The only honest measure is forward paper trading against a buy-and-hold SPY benchmark, tracked via `report.py`.
