"""
Scenario: deep-loss position should trigger a SELL recommendation.

Injects an AMZN position with an entry price well above current market value
so the model sees a meaningful unrealised loss.  Current prices are refreshed
from live Alpaca data inside run_scenario(), so the P/L in the log is accurate.

Writes to logs/test_decisions.jsonl (separate from live trading log).

Usage:
    python scenarios/test_sell_path.py
"""

import os
import sys

# Allow imports from the project root when running as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from logger import setup_logging
setup_logging()

from main import run_scenario

# Entry price set well above today's AMZN (~$246) to force a clear loss signal.
# run_scenario() will refresh current_price / unrealized_pl from live data.
FAKE_ACCOUNT = {
    "equity":       100_000.0,
    "cash":          88_400.0,
    "buying_power": 353_600.0,
    "last_equity":  100_000.0,
    "positions": [
        {
            "symbol":          "AMZN",
            "qty":             40.0,
            "avg_entry_price": 290.00,   # well above current market
            "current_price":   246.00,   # refreshed by run_scenario()
            "market_value":    9840.00,
            "unrealized_pl":   -1760.00,
            "unrealized_plpc": -15.17,
        }
    ],
}

run_scenario(
    description="sell_path: AMZN entry=$290 deeply underwater, expect SELL",
    fake_account=FAKE_ACCOUNT,
)
