import logging
import time
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

import config

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0


def _retry(label: str, fn):
    """Call fn() up to _MAX_ATTEMPTS times with exponential backoff. Raises on final failure."""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            log.warning(
                "%s attempt %d/%d failed: %s — retrying in %.0fs.",
                label, attempt + 1, _MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)


def _compute_atr(bars: list) -> float | None:
    """
    Simple ATR over the last config.ATR_PERIOD bars.
    True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    Returns the dollar ATR, or None if insufficient data.
    """
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        high = float(bars[i].high)
        low = float(bars[i].low)
        prev_close = float(bars[i - 1].close)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    period = min(config.ATR_PERIOD, len(trs))
    return sum(trs[-period:]) / period


def get_price_stats(symbols: list, api_key: str, secret_key: str) -> dict:
    """
    Returns per-symbol dict with price changes, ATR, and a ready-to-use
    atr_trail_pct for the trailing stop.  Missing / bad symbols are omitted
    (callers treat absence as "force hold").
    """
    client = StockHistoricalDataClient(api_key, secret_key)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=config.PRICE_LOOKBACK_DAYS + 10)

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )

    try:
        bars_data = _retry("StockBars fetch", lambda: client.get_stock_bars(request))
    except Exception as e:
        log.error("Price bar fetch failed after %d attempts: %s", _MAX_ATTEMPTS, e)
        return {}

    stats = {}
    for symbol in symbols:
        try:
            symbol_bars = bars_data[symbol]
        except (KeyError, TypeError):
            log.warning("No bar data returned for %s — forcing hold.", symbol)
            continue

        closes = [float(bar.close) for bar in symbol_bars]
        if not closes:
            log.warning("Empty bar list for %s — forcing hold.", symbol)
            continue

        current = closes[-1]

        if current <= 0:
            log.warning("Absurd price for %s (%.4f ≤ 0) — forcing hold.", symbol, current)
            continue

        chg_1d  = (closes[-1] / closes[-2] - 1) if len(closes) >= 2 else 0.0
        chg_5d  = (closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
        chg_30d = (closes[-1] / closes[0]  - 1) if len(closes) >= 2 else 0.0

        if abs(chg_1d) * 100 > config.MAX_1D_MOVE_PCT:
            log.warning(
                "Implausible 1-day move for %s (%.1f%%) — forcing hold.",
                symbol, chg_1d * 100,
            )
            continue

        # ATR-based trailing stop
        atr = _compute_atr(symbol_bars)
        if atr is not None and current > 0:
            raw_trail_pct = (config.ATR_MULTIPLIER * atr / current) * 100
            atr_trail_pct = max(config.ATR_TRAIL_MIN_PCT,
                                min(config.ATR_TRAIL_MAX_PCT, raw_trail_pct))
        else:
            atr_trail_pct = config.TRAILING_STOP_PCT * 100  # flat fallback

        stats[symbol] = {
            "current_price":   round(current, 4),
            "change_1d_pct":   round(chg_1d  * 100, 2),
            "change_5d_pct":   round(chg_5d  * 100, 2),
            "change_30d_pct":  round(chg_30d * 100, 2),
            "atr_14":          round(atr, 4) if atr is not None else None,
            "atr_trail_pct":   round(atr_trail_pct, 2),
        }

    return stats


def get_account_snapshot(api_key: str, secret_key: str) -> dict:
    """Returns equity, cash, buying_power, last_equity, and open positions from Alpaca."""
    client = TradingClient(api_key, secret_key, paper=True)

    account       = _retry("Account fetch",   client.get_account)
    raw_positions = _retry("Positions fetch", client.get_all_positions)

    positions = []
    for p in raw_positions:
        positions.append({
            "symbol":          p.symbol,
            "qty":             float(p.qty),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price":   float(p.current_price),
            "market_value":    float(p.market_value),
            "unrealized_pl":   float(p.unrealized_pl),
            "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2),
        })

    return {
        "equity":        float(account.equity),
        "cash":          float(account.cash),
        "buying_power":  float(account.buying_power),
        "last_equity":   float(account.last_equity),
        "positions":     positions,
    }
