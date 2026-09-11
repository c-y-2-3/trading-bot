import config


def build_prompt(account: dict, price_stats: dict, news: dict) -> str:
    lines: list = []

    lines.append("## Risk Limits")
    lines.append(f"- Max single-position size: {config.MAX_POSITION_PCT * 100:.0f}% of equity")
    lines.append(f"- Confidence-weighted sizing: actual size = MAX_POSITION_PCT × your confidence score")
    lines.append(f"- Confidence floor: buys below {config.CONFIDENCE_FLOOR:.0%} confidence are skipped entirely")
    lines.append(f"- Max total invested: {config.MAX_TOTAL_INVESTED_PCT * 100:.0f}% of equity")
    lines.append(
        f"- Daily loss halt: new buys blocked if equity drops "
        f">{config.DAILY_LOSS_HALT_PCT * 100:.0f}% vs previous close"
    )
    lines.append(
        f"- Broker-side trailing stop: ATR-based (≈{config.ATR_MULTIPLIER:.0f}×ATR, "
        f"clamped {config.ATR_TRAIL_MIN_PCT:.0f}–{config.ATR_TRAIL_MAX_PCT:.0f}%) "
        f"will be attached to every buy"
    )
    lines.append("")

    lines.append("## Account Snapshot")
    lines.append(f"- Equity:        ${account.get('equity', 0):>12,.2f}")
    lines.append(f"- Cash:          ${account.get('cash', 0):>12,.2f}")
    lines.append(f"- Buying power:  ${account.get('buying_power', 0):>12,.2f}")
    lines.append("")

    # ── Open positions ────────────────────────────────────────────────────────
    positions = account.get("positions", [])
    pos_map = {p["symbol"]: p for p in positions}

    if positions:
        lines.append("## Open Positions — Thesis Review Required")
        lines.append(
            "For EACH position below, you MUST decide: SELL or HOLD.\n"
            "  • SELL if: thesis is broken, news is clearly negative, or the loss is unacceptable.\n"
            "  • HOLD only if you can state a specific reason the thesis still holds.\n"
            "  • This bot never goes short — minimum quantity after a sell is 0 (full close).\n"
            "  • A trailing stop is already in place at broker level, but recommend SELL "
            "if the fundamental picture has changed."
        )
        lines.append("")
        for p in positions:
            atr_info = ""
            s = price_stats.get(p["symbol"], {})
            if s.get("atr_trail_pct") is not None:
                atr_info = f"  stop-trail={s['atr_trail_pct']:.1f}%"
            lines.append(
                f"  {p['symbol']}: {p['qty']:.0f} sh"
                f" @ ${p['avg_entry_price']:.2f} entry"
                f"  now ${p['current_price']:.2f}"
                f"  P/L ${p['unrealized_pl']:+.2f} ({p['unrealized_plpc']:+.2f}%)"
                f"{atr_info}"
            )
        lines.append("")
    else:
        lines.append("## Open Positions")
        lines.append("- No open positions")
        lines.append("")

    # ── Watchlist data ────────────────────────────────────────────────────────
    lines.append("## Watchlist Data")
    lines.append(
        "(Symbols marked [DATA UNAVAILABLE] must be HOLD with quantity 0.)"
    )

    for symbol in config.WATCHLIST:
        lines.append(f"\n### {symbol}")

        if symbol in pos_map:
            p = pos_map[symbol]
            lines.append(
                f"[CURRENTLY HELD: {p['qty']:.0f} sh  "
                f"P/L ${p['unrealized_pl']:+.2f} ({p['unrealized_plpc']:+.2f}%)]"
            )

        stats = price_stats.get(symbol)
        if stats:
            atr_str = ""
            if stats.get("atr_14") is not None:
                atr_str = (
                    f"  ATR(14)=${stats['atr_14']:.2f}"
                    f" → stop-trail={stats['atr_trail_pct']:.1f}%"
                )
            lines.append(
                f"Price: ${stats['current_price']:.2f}  "
                f"1d: {stats['change_1d_pct']:+.2f}%  "
                f"5d: {stats['change_5d_pct']:+.2f}%  "
                f"30d: {stats['change_30d_pct']:+.2f}%"
                f"{atr_str}"
            )
        else:
            lines.append("[DATA UNAVAILABLE — must HOLD]")

        symbol_news = news.get(symbol, [])
        if symbol_news:
            lines.append("Recent headlines:")
            for item in symbol_news:
                date_str = (item["created_at"] or "")[:10]
                lines.append(f"  [{item['source']} {date_str}] {item['headline']}")
                summary = (item.get("summary") or "").strip()
                if summary:
                    lines.append(f"    {summary[:250]}")
        else:
            lines.append("No recent news.")

    lines.append("")
    lines.append("## Instructions")
    lines.append(
        "Call the `trade_decision` tool with a decision for EVERY symbol in the watchlist.\n"
        "  • Held positions: SELL or HOLD — with explicit thesis reasoning.\n"
        "  • Non-held symbols: BUY or HOLD.\n"
        "  • quantity = 0 for holds; for sells, quantity = shares to close (full position unless partial exit justified).\n"
        "  • Set confidence honestly (0.0–1.0). Position size will be scaled to MAX_POSITION_PCT × confidence.\n"
        "  • Reasoning must cite specific price data, ATR/volatility context, or a named headline.\n"
        "  • Propose sizes that respect the risk limits above."
    )

    return "\n".join(lines)
