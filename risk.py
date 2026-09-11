import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)


def _kill_switch_active() -> bool:
    return Path("STOP").exists()


def apply_risk_checks(
    decisions: list,
    account: dict,
    price_stats: dict,
) -> list:
    """
    Filter and trim decisions against all hard risk limits (Tier 1) and
    confidence-weighted sizing rules (Tier 2).
    Returns the list of approved decisions (holds always pass through).
    Logs every adjustment or rejection with its reason.
    """
    if _kill_switch_active():
        log.warning("KILL SWITCH: STOP file exists — blocking all orders this cycle.")
        return [d for d in decisions if d.get("action") == "hold" or d.get("quantity", 0) == 0]

    equity       = float(account.get("equity", 0))
    buying_power = float(account.get("buying_power", 0))
    last_equity  = float(account.get("last_equity", equity))

    daily_return = (equity - last_equity) / last_equity if last_equity > 0 else 0.0
    halt_buys    = daily_return < -config.DAILY_LOSS_HALT_PCT
    if halt_buys:
        log.warning(
            "DAILY LOSS HALT: equity %.2f%% vs previous close (threshold %.0f%%). New buys blocked.",
            daily_return * 100, config.DAILY_LOSS_HALT_PCT * 100,
        )

    position_map     = {p["symbol"]: p for p in account.get("positions", [])}
    current_invested = sum(float(p["market_value"]) for p in account.get("positions", []))
    running_invested = current_invested
    running_bp       = buying_power

    approved = []

    for raw_decision in decisions:
        decision = dict(raw_decision)
        symbol   = decision.get("symbol", "")
        action   = decision.get("action", "hold")
        qty      = int(decision.get("quantity", 0))

        if action == "hold" or qty == 0:
            approved.append(decision)
            continue

        # ── BUY ──────────────────────────────────────────────────────────────
        if action == "buy":
            if halt_buys:
                log.info("REJECTED %s buy (qty=%d): daily loss halt active.", symbol, qty)
                continue

            # Confidence floor
            confidence = float(decision.get("confidence", 0.0))
            if confidence < config.CONFIDENCE_FLOOR:
                log.info(
                    "REJECTED %s buy: confidence %.0f%% below floor %.0f%%.",
                    symbol, confidence * 100, config.CONFIDENCE_FLOOR * 100,
                )
                continue

            # Resolve price
            pos = position_map.get(symbol)
            if pos:
                price = float(pos["current_price"])
            elif symbol in price_stats:
                price = float(price_stats[symbol]["current_price"])
            else:
                log.warning("REJECTED %s buy: no price data available.", symbol)
                continue

            # Confidence-weighted target size: target_pct = MAX_POSITION_PCT × confidence
            target_pct     = config.MAX_POSITION_PCT * confidence
            confidence_max = int(target_pct * equity / price)

            if confidence_max <= 0:
                log.info(
                    "REJECTED %s buy: confidence-weighted size (%.1f%% of equity) rounds to 0 shares.",
                    symbol, target_pct * 100,
                )
                continue

            if qty > confidence_max:
                log.info(
                    "SIZED %s buy %d→%d shares (confidence %.0f%% → target %.1f%% of equity).",
                    symbol, qty, confidence_max, confidence * 100, target_pct * 100,
                )
                qty = confidence_max
                decision = {**decision, "quantity": qty}

            order_value = qty * price

            # 1. Single-position cap
            pos_value_now = float(pos["market_value"]) if pos else 0.0
            new_pos_value = pos_value_now + order_value
            if equity > 0 and new_pos_value / equity > config.MAX_POSITION_PCT:
                headroom = config.MAX_POSITION_PCT * equity - pos_value_now
                if headroom <= 0:
                    log.info(
                        "REJECTED %s buy: already at/above %.0f%% position cap.",
                        symbol, config.MAX_POSITION_PCT * 100,
                    )
                    continue
                trimmed = int(headroom / price)
                if trimmed <= 0:
                    log.info("REJECTED %s buy: <1 share fits within position cap.", symbol)
                    continue
                log.info(
                    "TRIMMED %s buy %d→%d shares (position cap %.0f%%).",
                    symbol, qty, trimmed, config.MAX_POSITION_PCT * 100,
                )
                qty = trimmed
                order_value = qty * price
                decision = {**decision, "quantity": qty}

            # 2. Total-invested cap
            if equity > 0 and (running_invested + order_value) / equity > config.MAX_TOTAL_INVESTED_PCT:
                headroom = config.MAX_TOTAL_INVESTED_PCT * equity - running_invested
                if headroom <= 0:
                    log.info(
                        "REJECTED %s buy: total invested cap %.0f%% reached.",
                        symbol, config.MAX_TOTAL_INVESTED_PCT * 100,
                    )
                    continue
                trimmed = int(headroom / price)
                if trimmed <= 0:
                    log.info("REJECTED %s buy: <1 share fits within total invested cap.", symbol)
                    continue
                log.info(
                    "TRIMMED %s buy %d→%d shares (total invested cap %.0f%%).",
                    symbol, qty, trimmed, config.MAX_TOTAL_INVESTED_PCT * 100,
                )
                qty = trimmed
                order_value = qty * price
                decision = {**decision, "quantity": qty}

            # 3. Buying-power check
            if order_value > running_bp:
                trimmed = int(running_bp / price)
                if trimmed <= 0:
                    log.info(
                        "REJECTED %s buy: insufficient buying power ($%.2f).",
                        symbol, running_bp,
                    )
                    continue
                log.info(
                    "TRIMMED %s buy %d→%d shares (insufficient buying power $%.2f).",
                    symbol, qty, trimmed, running_bp,
                )
                qty = trimmed
                order_value = qty * price
                decision = {**decision, "quantity": qty}

            if qty <= 0:
                continue

            running_invested += order_value
            running_bp       -= order_value
            approved.append(decision)

        # ── SELL ─────────────────────────────────────────────────────────────
        elif action == "sell":
            pos = position_map.get(symbol)
            if pos is None:
                log.info("REJECTED %s sell: symbol not held.", symbol)
                continue

            held = int(float(pos["qty"]))
            if qty > held:
                log.info(
                    "TRIMMED %s sell %d→%d shares (only %d held).",
                    symbol, qty, held, held,
                )
                qty = held
                decision = {**decision, "quantity": qty}

            if qty <= 0:
                continue

            sell_value       = qty * float(pos.get("current_price", 0))
            running_invested = max(0.0, running_invested - sell_value)
            running_bp      += sell_value
            approved.append(decision)

    return approved
