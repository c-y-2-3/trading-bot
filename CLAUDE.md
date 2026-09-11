# Trading Bot — Operating Rules

1. **Paper trading only.** `paper=True` must always be set on the Alpaca client. Never switch to the live endpoint without explicit user instruction and a complete re-review.

2. **Dry-run is the default.** `--dry-run` must be passed explicitly to log decisions without placing orders. `--once` alone places real paper orders — always confirm this is intended.

3. **Risk is enforced in Python, never trusted to the model.** `risk.py` is the gatekeeper. The model proposes; `risk.py` disposes. Do not weaken or bypass risk checks.

4. **Never commit `.env`.** API keys live only in `.env` (gitignored). Rotate keys immediately if accidentally exposed.

5. **Always confirm before enabling live order placement.** Before removing the `dry_run` guard in `main.py` or `execution/trader.py`, stop and ask the user to review the pending decisions log first.
