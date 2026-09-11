WATCHLIST = [
    # Broad market ETFs
    "SPY", "QQQ", "IWM",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO",
    # Nuclear / energy
    "CEG", "VST", "CCJ", "URA",
    # Defense
    "LMT", "ITA",
    # Healthcare
    "LLY",
    # High-beta / speculative
    "PLTR", "COIN", "RKLB", "SMR", "OKLO",
    # Energy sector ETF
    "XLE",
]
MODEL = "claude-sonnet-4-6"
MAX_POSITION_PCT = 0.10
MAX_TOTAL_INVESTED_PCT = 0.80
DAILY_LOSS_HALT_PCT = 0.05
PRICE_LOOKBACK_DAYS = 30
MAX_NEWS_PER_SYMBOL = 5

# Broker-side protective stops (Tier 1)
USE_TRAILING_STOP = True
TRAILING_STOP_PCT = 0.08       # Flat fallback trail % when ATR is unavailable (8.0 to Alpaca)

# Data sanity checks
MAX_1D_MOVE_PCT = 25.0         # Force hold if |1-day move| exceeds this — likely a data glitch

# Tier 2: confidence-weighted sizing
CONFIDENCE_FLOOR = 0.55        # Reject buys below this confidence level

# Tier 2: ATR-based trailing stops
ATR_PERIOD = 14                # Rolling window for ATR computation
ATR_MULTIPLIER = 2.0           # Trail at 2× ATR below high-water mark
ATR_TRAIL_MIN_PCT = 3.0        # Never tighter than 3% (prevents over-trading on low-vol gaps)
ATR_TRAIL_MAX_PCT = 15.0       # Never wider than 15% (limits max loss on a very volatile name)
