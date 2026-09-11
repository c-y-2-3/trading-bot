"""
Scenario: model proposes buys with confidence below the floor (0.55).

The fake account has no positions.  We verify that low-confidence buys are
rejected by risk.py and do not appear in approved_decisions.

Writes to logs/test_decisions.jsonl.

Usage:
    python scenarios/test_confidence_floor.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from logger import setup_logging
setup_logging()

from main import run_scenario

# Fresh all-cash account — model will need to decide whether to buy
FAKE_ACCOUNT = {
    "equity":       100_000.0,
    "cash":         100_000.0,
    "buying_power": 400_000.0,
    "last_equity":  100_000.0,
    "positions":    [],
}

run_scenario(
    description="confidence_floor: verify buys below 55% confidence are rejected",
    fake_account=FAKE_ACCOUNT,
)
