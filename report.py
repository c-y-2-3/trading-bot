"""
report.py — print bot equity curve vs SPY benchmark from logs/decisions.jsonl

Usage:
    python report.py               # summary table (real cycles only)
    python report.py --all         # include dry-run cycles
    python report.py --tests       # show test scenarios instead
"""

import argparse
import json
import sys
from pathlib import Path

from logger import DECISIONS_LOG, TESTS_LOG

_WIDTH = 72


def _load_records(path: Path, include_dry_run: bool = False) -> list:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not include_dry_run and rec.get("dry_run"):
            continue
        records.append(rec)
    return records


def _sparkline(values: list[float], width: int = 20) -> str:
    """Minimal ASCII sparkline using block characters."""
    blocks = " ▁▂▃▄▅▆▇█"
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    chars = [blocks[min(8, int((v - lo) / span * 8))] for v in values]
    # pad or truncate to width
    chars = chars[-width:] if len(chars) > width else chars
    return "".join(chars)


def _pct(a: float, b: float) -> str:
    if b == 0:
        return "  N/A"
    return f"{(a / b - 1) * 100:+.2f}%"


def print_report(records: list, title: str) -> None:
    print()
    print("=" * _WIDTH)
    print(f"  {title}")
    print("=" * _WIDTH)

    if not records:
        print("  No records found.")
        print("=" * _WIDTH)
        return

    # Table header
    print(
        f"  {'Date/Time':<20}  {'Bot Equity':>13}  {'SPY Bench':>13}  "
        f"{'vs SPY':>8}  {'Positions':>9}"
    )
    print("  " + "-" * (_WIDTH - 2))

    equity_series: list[float] = []
    spy_series:    list[float] = []

    for rec in records:
        ts        = rec.get("timestamp", "")[:16].replace("T", " ")
        acct      = rec.get("account_before", {})
        equity    = acct.get("equity", 0.0)
        n_pos     = len(acct.get("positions", []))
        bm        = rec.get("benchmark") or {}
        spy_val   = bm.get("spy_benchmark_value")
        vs_pct    = f"{bm.get('vs_benchmark_pct', 0):+.2f}%" if bm else "  —"
        spy_str   = f"${spy_val:>11,.2f}" if spy_val else "          —"
        scen      = f"  [{rec['scenario']}]" if rec.get("scenario") else ""

        print(
            f"  {ts:<20}  ${equity:>12,.2f}  {spy_str}  {vs_pct:>8}  {n_pos:>5} pos{scen}"
        )

        equity_series.append(equity)
        if spy_val:
            spy_series.append(spy_val)

    print("  " + "-" * (_WIDTH - 2))

    # Summary row
    if equity_series:
        first_eq = equity_series[0]
        last_eq  = equity_series[-1]
        total_pct = f"{(last_eq / first_eq - 1) * 100:+.2f}%" if first_eq else "N/A"
        print(f"\n  Total bot return:  {total_pct}  (${first_eq:,.2f} → ${last_eq:,.2f})")

    if spy_series and len(spy_series) == len(equity_series):
        first_spy = spy_series[0]
        last_spy  = spy_series[-1]
        spy_pct   = f"{(last_spy / first_spy - 1) * 100:+.2f}%" if first_spy else "N/A"
        print(f"  Total SPY return:  {spy_pct}  (${first_spy:,.2f} → ${last_spy:,.2f})")
        alpha = (last_eq / first_eq - last_spy / first_spy) * 100 if first_eq and first_spy else 0
        print(f"  Alpha vs SPY:      {alpha:+.2f}%")

    # Sparklines
    if len(equity_series) >= 3:
        print(f"\n  Bot equity  [{_sparkline(equity_series)}]")
    if len(spy_series) >= 3:
        print(f"  SPY bench   [{_sparkline(spy_series)}]")

    print("=" * _WIDTH + "\n")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Bot equity vs SPY benchmark report")
    parser.add_argument("--all",   action="store_true", help="Include dry-run cycles")
    parser.add_argument("--tests", action="store_true", help="Show test scenarios (test_decisions.jsonl)")
    args = parser.parse_args()

    if args.tests:
        records = _load_records(TESTS_LOG, include_dry_run=True)
        print_report(records, "TEST SCENARIOS  —  logs/test_decisions.jsonl")
    else:
        records = _load_records(DECISIONS_LOG, include_dry_run=args.all)
        label   = "ALL CYCLES (including dry-run)" if args.all else "LIVE + DRY-RUN CYCLES"
        print_report(records, f"BOT vs SPY BENCHMARK  —  {label}")


if __name__ == "__main__":
    main()
