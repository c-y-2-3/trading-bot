import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR         = Path("logs")
DECISIONS_LOG    = LOGS_DIR / "decisions.jsonl"
TESTS_LOG        = LOGS_DIR / "test_decisions.jsonl"


def setup_logging() -> None:
    # Force UTF-8 on Windows consoles so non-ASCII in model responses don't crash
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    LOGS_DIR.mkdir(exist_ok=True)


def log_cycle(
    *,
    mode: str,
    dry_run: bool,
    account_before: dict,
    raw_decision: dict,
    approved_decisions: list,
    orders_placed: list,
    errors: list,
    benchmark: dict | None = None,
    test_run: bool = False,
    scenario: str | None = None,
) -> None:
    record = {
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "mode":               mode,
        "dry_run":            dry_run,
        "test_run":           test_run,
        "account_before":     account_before,
        "raw_decision":       raw_decision,
        "approved_decisions": approved_decisions,
        "orders_placed":      orders_placed,
        "errors":             errors,
    }
    if benchmark is not None:
        record["benchmark"] = benchmark
    if scenario is not None:
        record["scenario"] = scenario

    LOGS_DIR.mkdir(exist_ok=True)
    log_path = TESTS_LOG if test_run else DECISIONS_LOG
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    _print_summary(record)
    return record


def _print_summary(record: dict) -> None:
    is_test = record.get("test_run", False)
    header  = "TEST SCENARIO" if is_test else "CYCLE SUMMARY"
    scenario_label = f"  [{record['scenario']}]" if record.get("scenario") else ""

    print("\n" + "=" * 62)
    print(f"  {header}{scenario_label}  {record['timestamp']}")
    print(f"  Mode: {record['mode']}  |  Dry-run: {record['dry_run']}")
    print("=" * 62)

    acct = record.get("account_before", {})
    if acct:
        print(
            f"Account  equity=${acct.get('equity', 0):>12,.2f}"
            f"  cash=${acct.get('cash', 0):>12,.2f}"
            f"  bp=${acct.get('buying_power', 0):>12,.2f}"
        )
        positions = acct.get("positions", [])
        if positions:
            print(f"Positions ({len(positions)}):")
            for p in positions:
                print(
                    f"  {p['symbol']:<6} {p['qty']:>8.0f} sh"
                    f"  entry=${p['avg_entry_price']:>8.2f}"
                    f"  now=${p['current_price']:>8.2f}"
                    f"  P/L ${p['unrealized_pl']:>+10.2f} ({p['unrealized_plpc']:>+.2f}%)"
                )
        else:
            print("Positions: none")

    # Benchmark
    bm = record.get("benchmark")
    if bm:
        print(
            f"Benchmark  SPY={bm.get('spy_shares', 0):.2f} sh"
            f" × ${bm.get('spy_price_now', 0):.2f}"
            f" = ${bm.get('spy_benchmark_value', 0):,.2f}"
            f"  bot vs SPY: {bm.get('vs_benchmark_pct', 0):+.2f}%"
        )

    decisions = record.get("raw_decision", {}).get("decisions", [])
    print(f"\nModel decisions ({len(decisions)}):")
    for d in decisions:
        action  = d.get("action", "?").upper()
        symbol  = d.get("symbol", "?")
        qty     = d.get("quantity", 0)
        conf    = d.get("confidence", 0)
        qty_str = f"x{qty}" if action != "HOLD" else "     "
        print(f"  [{action:<4}] {symbol:<6} {qty_str:<6}  conf={conf:.0%}")
        reasoning = d.get("reasoning", "")
        if reasoning:
            print(f"           {reasoning}")

    comment = record.get("raw_decision", {}).get("portfolio_comment", "")
    if comment:
        print(f"\nPortfolio: {comment}")

    approved = record.get("approved_decisions", [])
    active   = [d for d in approved if d.get("action") != "hold" and d.get("quantity", 0) > 0]
    print(f"\nApproved orders: {len(active)}")
    for d in active:
        print(f"  {d['action'].upper()} {d['quantity']}x {d['symbol']}")

    placed = record.get("orders_placed", [])
    if placed:
        print(f"Orders placed: {len(placed)}")
        for o in placed:
            print(f"  {o}")

    errs = record.get("errors", [])
    if errs:
        print(f"\nErrors ({len(errs)}):")
        for e in errs:
            print(f"  {e}")

    print("=" * 62 + "\n")
