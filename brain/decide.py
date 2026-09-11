import logging

import anthropic

import config
from brain.prompt import build_prompt

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a disciplined, risk-aware portfolio assistant managing a paper trading account "
    "with a multi-day investment horizon. You analyse price data and recent news to propose "
    "sensible trades that stay strictly within the stated risk limits. Prioritise capital "
    "preservation; only take high-confidence opportunities. This is paper money, but treat "
    "decisions as if they were real. You MUST respond by calling the trade_decision tool — "
    "do not include any prose outside the tool call."
)

_TOOL = {
    "name": "trade_decision",
    "description": "Submit trade decisions for every watchlist symbol in this cycle.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "description": "One entry per watchlist symbol.",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "action": {
                            "type": "string",
                            "enum": ["buy", "sell", "hold"],
                        },
                        "quantity": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Number of shares. Must be 0 for hold.",
                        },
                        "order_type": {
                            "type": "string",
                            "enum": ["market"],
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "1-3 sentences citing specific price action or a named headline.",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["symbol", "action", "quantity", "order_type", "reasoning", "confidence"],
                },
            },
            "portfolio_comment": {
                "type": "string",
                "description": "1-2 sentence overall view of positioning.",
            },
        },
        "required": ["decisions", "portfolio_comment"],
    },
}


def _call_api(client: anthropic.Anthropic, messages: list) -> anthropic.types.Message:
    return client.messages.create(
        model=config.MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "trade_decision"},
        messages=messages,
    )


def _extract_tool_input(response: anthropic.types.Message) -> dict | None:
    for block in response.content:
        if block.type == "tool_use" and block.name == "trade_decision":
            return block.input
    return None


def _validate_decisions(decisions: list, held_positions: dict) -> list:
    """
    Post-schema validation that the tool_use schema cannot enforce:
    - Drop symbols not in the watchlist
    - Clamp quantity to >= 0
    - Cap sell quantity at held quantity (risk.py also does this, but warn early)
    """
    watchlist_set = set(config.WATCHLIST)
    clean = []
    for d in decisions:
        symbol = d.get("symbol", "")
        action = d.get("action", "hold")
        qty = d.get("quantity", 0)

        if symbol not in watchlist_set:
            log.warning("Model returned off-watchlist symbol '%s' — dropping.", symbol)
            continue

        if qty < 0:
            log.warning("Negative quantity %d for %s — clamping to 0.", qty, symbol)
            d = {**d, "quantity": 0, "action": "hold"}

        if action == "sell":
            held_qty = int(float(held_positions.get(symbol, {}).get("qty", 0)))
            if held_qty == 0:
                log.warning(
                    "Model proposed sell for %s but position not held — converting to hold.", symbol
                )
                d = {**d, "action": "hold", "quantity": 0}
            elif qty > held_qty:
                log.warning(
                    "Sell quantity %d for %s exceeds held %d — clamping.", qty, symbol, held_qty
                )
                d = {**d, "quantity": held_qty}

        clean.append(d)
    return clean


def get_trade_decision(
    account: dict,
    price_stats: dict,
    news: dict,
    api_key: str,
) -> dict:
    """
    Calls Claude and returns the parsed, validated trade decision dict.
    Retries once with a stricter prompt on failure.
    Raises RuntimeError if both attempts fail.
    """
    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = build_prompt(account, price_stats, news)
    messages = [{"role": "user", "content": user_prompt}]

    held_positions = {p["symbol"]: p for p in account.get("positions", [])}

    log.info("Sending decision request to %s ...", config.MODEL)
    response = _call_api(client, messages)
    result = _extract_tool_input(response)

    if result is None:
        log.warning("No trade_decision tool call in first response — retrying with stricter prompt.")
        messages.append({"role": "assistant", "content": response.content})
        messages.append(
            {
                "role": "user",
                "content": (
                    "You did not call the trade_decision tool. "
                    "You MUST call it now with a decision for every watchlist symbol."
                ),
            }
        )
        response = _call_api(client, messages)
        result = _extract_tool_input(response)

    if result is None:
        raise RuntimeError("Model failed to return a trade_decision tool call after two attempts.")

    decisions = result.get("decisions", [])
    decisions = _validate_decisions(decisions, held_positions)
    result = {**result, "decisions": decisions}

    log.info("Decision received: %d symbols.", len(decisions))
    return result
