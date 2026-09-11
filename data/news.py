import logging
import time
from datetime import datetime, timedelta, timezone

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

import config

log = logging.getLogger(__name__)

NEWS_LOOKBACK_DAYS = 3
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0


def _retry(label: str, fn):
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


def get_news(symbols: list, api_key: str, secret_key: str) -> dict:
    """
    Returns {symbol: [{"headline", "summary", "source", "created_at"}, ...]}
    Explicit start date is required — without it the API returns nothing.
    Per-symbol failures produce empty lists rather than raising.
    """
    client = NewsClient(api_key, secret_key)
    start = datetime.now(timezone.utc) - timedelta(days=NEWS_LOOKBACK_DAYS)
    news_by_symbol: dict = {}

    for symbol in symbols:
        try:
            request = NewsRequest(
                symbols=symbol,
                limit=config.MAX_NEWS_PER_SYMBOL,
                sort="desc",
                start=start,
            )
            response = _retry(
                f"News fetch ({symbol})",
                lambda req=request: client.get_news(req),
            )

            articles = response.data.get("news", []) if hasattr(response, "data") else []

            items = []
            for article in articles[: config.MAX_NEWS_PER_SYMBOL]:
                created = getattr(article, "created_at", None)
                items.append(
                    {
                        "headline": getattr(article, "headline", ""),
                        "summary": (getattr(article, "summary", "") or "").strip(),
                        "source": getattr(article, "source", ""),
                        "created_at": created.isoformat() if created else None,
                    }
                )
            news_by_symbol[symbol] = items
            log.info("News for %s: %d article(s).", symbol, len(items))

        except Exception as e:
            log.warning("News fetch for %s failed after %d attempts: %s", symbol, _MAX_ATTEMPTS, e)
            news_by_symbol[symbol] = []

    return news_by_symbol
