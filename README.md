# Paper Trading Bot

A daily paper-trading bot for US stocks/ETFs. Claude analyses price data and news, proposes trades, and risk checks gate every decision before any order is placed.

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

**Start with `--once --dry-run` and review `logs/decisions.jsonl` before enabling order placement.**

## Emergency stop

Create a file named `STOP` in the project root. The bot checks for it before placing any order and will skip the cycle if found.

## Risk limits (edit `config.py`)

- Max 10% of equity in any single symbol
- Max 80% of equity total invested
- Halt new buys if equity drops >5% intraday vs previous close
