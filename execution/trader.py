import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

import config
from execution.stops import _compute_stop_price, _resolve_trail_pct, attach_trailing_stop

log = logging.getLogger(__name__)


def place_orders(
    decisions: list,
    api_key: str,
    secret_key: str,
    dry_run: bool = True,
    price_stats: dict = None,
) -> list:
    """
    Place paper market orders for every approved buy/sell decision.

    Buys when the market is open are submitted as OTO (one-triggers-other)
    orders: a single atomic request containing both the market buy and a
    fixed stop-loss leg at the ATR-computed distance.  This eliminates the
    race condition where the stop attachment was rejected because the buy
    was still PENDING_NEW.

    When the market is closed (clock check failed → OPG fallback), a plain
    market order is placed instead; reconcile_stops() picks up any missing
    stops at the start of the next cycle.

    In dry_run=True mode: logs intended orders and places nothing.
    """
    price_stats = price_stats or {}

    actionable = [
        d for d in decisions
        if d.get("action") in ("buy", "sell") and d.get("quantity", 0) > 0
    ]

    if not actionable:
        log.info("No actionable orders to place.")
        return []

    if dry_run:
        log.info("DRY RUN — would place %d order(s):", len(actionable))
        for d in actionable:
            symbol = d["symbol"]
            qty    = d["quantity"]
            action = d["action"].upper()
            if d["action"] == "buy" and config.USE_TRAILING_STOP:
                stop_price = _compute_stop_price(symbol, price_stats)
                trail_pct  = _resolve_trail_pct(symbol, price_stats)
                if stop_price:
                    log.info(
                        "  %s %dx %s @ market (OTO stop @ $%.2f = %.1f%% below entry)",
                        action, qty, symbol, stop_price, trail_pct,
                    )
                else:
                    log.info("  %s %dx %s @ market (no price for stop — plain order)", action, qty, symbol)
            else:
                log.info("  %s %dx %s @ market", action, qty, symbol)
        return []

    client = TradingClient(api_key, secret_key, paper=True)

    try:
        clock = client.get_clock()
        market_open = clock.is_open
    except Exception as e:
        log.warning("Could not fetch market clock: %s — assuming closed, using OPG.", e)
        market_open = False

    placed = []
    for d in actionable:
        symbol = d["symbol"]
        qty    = d["quantity"]
        action = d["action"]
        side   = OrderSide.BUY if action == "buy" else OrderSide.SELL
        tif    = TimeInForce.DAY if market_open else TimeInForce.OPG

        try:
            # Buys during market hours: OTO order (atomic entry + stop-loss leg).
            # Alpaca does not support trailing stops as bracket legs, so we use a
            # fixed stop at the ATR-computed distance from current price.
            if action == "buy" and config.USE_TRAILING_STOP and market_open:
                stop_price = _compute_stop_price(symbol, price_stats)
                trail_pct  = _resolve_trail_pct(symbol, price_stats)
                if stop_price:
                    request = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY,
                        order_class=OrderClass.OTO,
                        stop_loss=StopLossRequest(stop_price=stop_price),
                    )
                    order = client.submit_order(request)
                    log.info(
                        "ORDER PLACED: BUY %dx %s (OTO stop @ $%.2f = %.1f%% below entry)"
                        "  id=%s  status=%s",
                        qty, symbol, stop_price, trail_pct, order.id, order.status,
                    )
                    placed.append({
                        "symbol":     symbol,
                        "action":     action,
                        "quantity":   qty,
                        "order_id":   str(order.id),
                        "status":     str(order.status),
                        "stop_price": stop_price,
                    })
                    continue

            # Sells, OPG fallback, or stops disabled: plain market order.
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
            )
            order = client.submit_order(request)
            log.info(
                "ORDER PLACED: %s %dx %s  id=%s  status=%s",
                action.upper(), qty, symbol, order.id, order.status,
            )
            placed.append({
                "symbol":   symbol,
                "action":   action,
                "quantity": qty,
                "order_id": str(order.id),
                "status":   str(order.status),
            })

            # OPG buy without OTO: attach trailing stop after fill on next reconcile.
            # For in-hours buys that had no price data, attach stop immediately.
            if action == "buy" and config.USE_TRAILING_STOP and not market_open:
                log.info(
                    "OPG order for %s — stop will be attached by reconcile_stops() next cycle.", symbol
                )

        except Exception as e:
            log.error("ORDER FAILED: %s %dx %s — %s", action.upper(), qty, symbol, e)

    return placed
