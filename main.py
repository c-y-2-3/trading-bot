import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import config
import notify
from logger import DECISIONS_LOG, log_cycle, setup_logging

log = logging.getLogger(__name__)

_BENCHMARK_STATE = Path(".benchmark_state.json")


# ── Startup banner ────────────────────────────────────────────────────────────

def _banner(mode: str, dry_run: bool) -> None:
    print()
    print("=" * 62)
    print("  PAPER TRADING BOT  —  paper=True  —  NEVER live endpoint")
    print(f"  Mode:      {mode}")
    print(f"  Dry-run:   {dry_run}  ({'log only, NO orders' if dry_run else 'WILL PLACE PAPER ORDERS'})")
    print(f"  Watchlist: {', '.join(config.WATCHLIST)}")
    print(f"  Model:     {config.MODEL}")
    print("  Risk limits:")
    print(f"    Max position    {config.MAX_POSITION_PCT*100:.0f}% × confidence per symbol")
    print(f"    Confidence floor {config.CONFIDENCE_FLOOR:.0%} — buys below this are skipped")
    print(f"    Max invested    {config.MAX_TOTAL_INVESTED_PCT*100:.0f}% of equity total")
    print(f"    Daily halt      halt buys if equity down >{config.DAILY_LOSS_HALT_PCT*100:.0f}%")
    stop_label = (
        f"ATR×{config.ATR_MULTIPLIER:.0f} trail "
        f"({config.ATR_TRAIL_MIN_PCT:.0f}–{config.ATR_TRAIL_MAX_PCT:.0f}% range)"
        if config.USE_TRAILING_STOP else "disabled"
    )
    print(f"    Broker stop     {stop_label} (GTC, broker-side)")
    print("=" * 62)
    print()


def _require_env(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"Missing required env vars: {', '.join(missing)}  — add them to .env")


# ── Cycle guards ──────────────────────────────────────────────────────────────

def _is_trading_day(api_key: str, secret_key: str) -> bool:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest

    client = TradingClient(api_key, secret_key, paper=True)
    today = date.today()
    try:
        calendar = client.get_calendar(GetCalendarRequest(start=today, end=today))
        return len(calendar) > 0
    except Exception as e:
        log.warning("Could not fetch trading calendar: %s — assuming trading day.", e)
        return True


def _already_ran_today(api_key: str, secret_key: str) -> bool:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import OrderSide, OrderType, QueryOrderStatus

    client = TradingClient(api_key, secret_key, paper=True)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        orders = client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, after=today_start)
        )
        buy_orders = [
            o for o in orders
            if getattr(o, "side", None) == OrderSide.BUY
            and getattr(o, "type", None) == OrderType.MARKET
        ]
        if buy_orders:
            log.warning(
                "Found %d buy order(s) already placed today — skipping to prevent double-trading.",
                len(buy_orders),
            )
            return True
        return False
    except Exception as e:
        log.warning("Could not check today's orders: %s — proceeding cautiously.", e)
        return False


# ── Position reconciliation ───────────────────────────────────────────────────

def _load_last_positions() -> list:
    if not DECISIONS_LOG.exists():
        return []
    try:
        with open(DECISIONS_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            f.seek(max(0, size - 16384))
            chunk = f.read().decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            return []
        record = json.loads(lines[-1])
        return record.get("account_before", {}).get("positions", [])
    except Exception as e:
        log.warning("Could not load last run positions from log: %s", e)
        return []


def _detect_closed_positions(prev_positions: list, current_positions: list) -> None:
    if not prev_positions:
        return
    current_symbols = {p["symbol"] for p in current_positions}
    for p in prev_positions:
        if p["symbol"] not in current_symbols:
            log.info(
                "POSITION CLOSED since last run: %s  (was %g sh @ $%.2f entry, last P/L $%+.2f)",
                p["symbol"], p["qty"], p["avg_entry_price"], p["unrealized_pl"],
            )


# ── SPY benchmark tracking ────────────────────────────────────────────────────

def _load_benchmark_state() -> dict | None:
    if not _BENCHMARK_STATE.exists():
        return None
    try:
        return json.loads(_BENCHMARK_STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _init_benchmark(equity: float, price_stats: dict) -> dict | None:
    """
    Create benchmark state on the first real run.
    Records how many SPY shares we would have bought with the starting equity.
    """
    spy_price = price_stats.get("SPY", {}).get("current_price")
    if not spy_price:
        log.warning("SPY price unavailable — benchmark not initialised this cycle.")
        return None
    spy_shares = equity / spy_price
    state = {
        "initial_equity":      equity,
        "spy_shares":          spy_shares,
        "start_date":          date.today().isoformat(),
        "spy_price_at_start":  spy_price,
    }
    _BENCHMARK_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.info(
        "Benchmark initialised: %.4f SPY shares @ $%.2f (equity $%.2f on %s).",
        spy_shares, spy_price, equity, state["start_date"],
    )
    return state


def _compute_benchmark(state: dict, price_stats: dict, bot_equity: float) -> dict | None:
    spy_price = price_stats.get("SPY", {}).get("current_price")
    if not spy_price:
        return None
    spy_value = state["spy_shares"] * spy_price
    vs_pct    = (bot_equity / spy_value - 1) * 100 if spy_value > 0 else 0.0
    return {
        "spy_shares":          state["spy_shares"],
        "spy_price_now":       spy_price,
        "spy_benchmark_value": round(spy_value, 2),
        "bot_equity":          round(bot_equity, 2),
        "vs_benchmark_pct":    round(vs_pct, 2),
    }


# ── Main cycle ────────────────────────────────────────────────────────────────

def run_cycle(dry_run: bool) -> None:
    from data.prices import get_account_snapshot, get_price_stats
    from data.news import get_news
    from brain.decide import get_trade_decision
    from risk import apply_risk_checks
    from execution.trader import place_orders
    from execution.stops import reconcile_stops
    from alpaca.trading.client import TradingClient

    alpaca_key    = os.environ["ALPACA_API_KEY"]
    alpaca_secret = os.environ["ALPACA_SECRET_KEY"]
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    account: dict            = {}
    price_stats: dict        = {}
    news: dict               = {}
    raw_decision: dict       = {}
    approved_decisions: list = []
    orders_placed: list      = []
    errors: list             = []
    benchmark: dict | None   = None

    try:
        # Guard 1: market calendar
        if not _is_trading_day(alpaca_key, alpaca_secret):
            log.info("Not a trading day — skipping cycle.")
            notify.send_alert(
                "Skipped: not a trading day",
                f"{date.today().isoformat()} — market closed or holiday.",
            )
            return

        # Guard 2: idempotency (live mode only)
        if not dry_run and _already_ran_today(alpaca_key, alpaca_secret):
            notify.send_alert(
                "Skipped: already ran today",
                "Idempotency guard — buy orders already placed today.",
            )
            return

        log.info("Fetching account snapshot ...")
        account = get_account_snapshot(alpaca_key, alpaca_secret)

        # Position reconciliation
        prev_positions = _load_last_positions()
        _detect_closed_positions(prev_positions, account["positions"])

        log.info("Fetching price bars ...")
        price_stats = get_price_stats(config.WATCHLIST, alpaca_key, alpaca_secret)

        if config.USE_TRAILING_STOP and account["positions"]:
            log.info("Reconciling broker-side trailing stops ...")
            trading_client = TradingClient(alpaca_key, alpaca_secret, paper=True)
            reconcile_stops(trading_client, account["positions"], price_stats, dry_run)

        log.info("Fetching news ...")
        news = get_news(config.WATCHLIST, alpaca_key, alpaca_secret)

        # Benchmark tracking
        bm_state = _load_benchmark_state()
        if bm_state is None:
            bm_state = _init_benchmark(account["equity"], price_stats)
        if bm_state is not None:
            benchmark = _compute_benchmark(bm_state, price_stats, account["equity"])

        log.info("Requesting trade decision from model ...")
        raw_decision = get_trade_decision(account, price_stats, news, anthropic_key)

        log.info("Applying risk checks ...")
        approved_decisions = apply_risk_checks(
            raw_decision.get("decisions", []),
            account,
            price_stats,
        )

        log.info("Placing orders (dry_run=%s) ...", dry_run)
        orders_placed = place_orders(
            approved_decisions,
            alpaca_key,
            alpaca_secret,
            dry_run=dry_run,
            price_stats=price_stats,
        )

    except Exception as exc:
        log.error("Cycle failed: %s", exc, exc_info=True)
        errors.append(str(exc))
        notify.send_alert("Cycle error", str(exc))

    record = log_cycle(
        mode="once",
        dry_run=dry_run,
        account_before=account,
        raw_decision=raw_decision,
        approved_decisions=approved_decisions,
        orders_placed=orders_placed,
        errors=errors,
        benchmark=benchmark,
    )
    notify.send_summary(record)


def run_scenario(description: str, fake_account: dict) -> None:
    """
    Run a full dry-run cycle with an injected account snapshot.
    Writes to logs/test_decisions.jsonl (not the live log).
    Use this for constructed test scenarios — keeps test data auditable
    and separate from real trading cycles.
    """
    from data.prices import get_price_stats
    from data.news import get_news
    from brain.decide import get_trade_decision
    from risk import apply_risk_checks
    from execution.trader import place_orders

    alpaca_key    = os.environ["ALPACA_API_KEY"]
    alpaca_secret = os.environ["ALPACA_SECRET_KEY"]
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    log.info("=== SCENARIO: %s ===", description)

    errors: list = []
    raw_decision: dict       = {}
    approved_decisions: list = []

    try:
        price_stats = get_price_stats(config.WATCHLIST, alpaca_key, alpaca_secret)

        # Refresh position prices from live data so the scenario is realistic
        for pos in fake_account.get("positions", []):
            live = price_stats.get(pos["symbol"], {})
            if live.get("current_price"):
                pos["current_price"] = live["current_price"]
                pos["market_value"]  = live["current_price"] * pos["qty"]
                pos["unrealized_pl"] = (live["current_price"] - pos["avg_entry_price"]) * pos["qty"]
                pos["unrealized_plpc"] = round(
                    (live["current_price"] / pos["avg_entry_price"] - 1) * 100, 2
                )

        news = get_news(config.WATCHLIST, alpaca_key, alpaca_secret)
        raw_decision = get_trade_decision(fake_account, price_stats, news, anthropic_key)
        approved_decisions = apply_risk_checks(
            raw_decision.get("decisions", []), fake_account, price_stats
        )
        place_orders(
            approved_decisions, alpaca_key, alpaca_secret,
            dry_run=True, price_stats=price_stats,
        )

    except Exception as exc:
        log.error("Scenario failed: %s", exc, exc_info=True)
        errors.append(str(exc))

    log_cycle(
        mode="scenario",
        dry_run=True,
        account_before=fake_account,
        raw_decision=raw_decision,
        approved_decisions=approved_decisions,
        orders_placed=[],
        errors=errors,
        test_run=True,
        scenario=description,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()
    setup_logging()

    parser = argparse.ArgumentParser(description="Paper trading bot")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--once", action="store_true", help="Run one cycle then exit")
    mode_group.add_argument("--loop", action="store_true", help="Run on a daily schedule (APScheduler)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + decide + log but place NO orders (safe default)",
    )
    args = parser.parse_args()

    _require_env("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY")

    if args.once:
        _banner("once", args.dry_run)
        run_cycle(dry_run=args.dry_run)

    elif args.loop:
        from apscheduler.schedulers.blocking import BlockingScheduler

        _banner("loop", args.dry_run)
        scheduler = BlockingScheduler(timezone="America/New_York")
        scheduler.add_job(
            run_cycle,
            trigger="cron",
            day_of_week="mon-fri",
            hour=9,
            minute=35,
            kwargs={"dry_run": args.dry_run},
        )
        log.info("Scheduler running — next cycle at 09:35 ET on the next weekday.")
        log.info("Press Ctrl+C to stop.")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
