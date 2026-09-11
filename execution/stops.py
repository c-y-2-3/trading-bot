import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, TrailingStopOrderRequest

import config

log = logging.getLogger(__name__)


def _resolve_trail_pct(symbol: str, price_stats: dict) -> float:
    """
    Return the ATR-based trail percent for symbol, falling back to the flat
    config value if ATR data is absent.  Value is in Alpaca's unit (8.0 = 8%).
    """
    return float(
        (price_stats or {}).get(symbol, {}).get("atr_trail_pct")
        or config.TRAILING_STOP_PCT * 100
    )


def _compute_stop_price(symbol: str, price_stats: dict) -> float | None:
    """
    Return the fixed stop price for an OTO bracket order: current price minus
    the ATR-based trail distance.  Returns None if current price is unavailable.
    """
    price = (price_stats or {}).get(symbol, {}).get("current_price")
    if not price:
        return None
    trail_pct = _resolve_trail_pct(symbol, price_stats)
    return round(price * (1 - trail_pct / 100), 2)


def attach_trailing_stop(
    client: TradingClient,
    symbol: str,
    qty: int,
    trail_pct: float,
    dry_run: bool,
) -> None:
    """
    Submit a GTC trailing-stop sell for `qty` shares of `symbol`.
    `trail_pct` is in Alpaca's unit (e.g. 5.2 for 5.2%).
    In dry-run: logs the intended order and returns without submitting.
    """
    if dry_run:
        log.info(
            "DRY RUN: would attach %.1f%% trailing stop sell for %dx %s (GTC).",
            trail_pct, qty, symbol,
        )
        return

    try:
        request = TrailingStopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            trail_percent=trail_pct,
        )
        order = client.submit_order(request)
        log.info(
            "TRAILING STOP attached: %dx %s @ %.1f%% trail  id=%s",
            qty, symbol, trail_pct, order.id,
        )
    except Exception as e:
        log.error("Failed to attach trailing stop for %s: %s", symbol, e)


def reconcile_stops(
    client: TradingClient,
    positions: list,
    price_stats: dict,
    dry_run: bool,
) -> None:
    """
    Ensure every open position has a GTC trailing-stop sell order.
    Attaches one for any position that is currently unprotected.
    Uses ATR-based trail percent per symbol (falls back to flat config).
    Safe to call every cycle — idempotent if the stop already exists.
    """
    if not positions:
        return

    symbols = [p["symbol"] for p in positions]

    try:
        open_orders = client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=symbols)
        )
    except Exception as e:
        log.warning("Could not fetch open orders for stop reconciliation: %s", e)
        return

    # Recognise both standalone trailing-stop orders and the fixed stop leg
    # created by OTO bracket orders (OrderType.STOP) as protected.
    protected = {
        o.symbol
        for o in open_orders
        if getattr(o, "side", None) == OrderSide.SELL
        and getattr(o, "type", None) in (OrderType.TRAILING_STOP, OrderType.STOP, OrderType.STOP_LIMIT)
    }

    for pos in positions:
        symbol    = pos["symbol"]
        qty       = int(float(pos["qty"]))
        trail_pct = _resolve_trail_pct(symbol, price_stats)

        if qty <= 0:
            continue
        if symbol in protected:
            log.debug("Trailing stop already in place for %s.", symbol)
        else:
            log.info(
                "No trailing stop for %s (qty=%d) — attaching %.1f%% trail.",
                symbol, qty, trail_pct,
            )
            attach_trailing_stop(client, symbol, qty, trail_pct, dry_run)
