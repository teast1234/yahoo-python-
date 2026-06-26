import json
import time
import urllib.error
import urllib.parse
import urllib.request

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from app.models import NewsArticle
from app.services.common import (
    DEFAULT_MARKET_QUERIES,
    RETRY_DELAYS_SECONDS,
    TAB_QUERY_REFS,
    VALID_TABS,
    YAHOO_NEWS_URL,
    YAHOO_SEARCH_URL,
    RateLimitError,
    extract_url,
    resolve_market_region,
    text_or_none,
)


def _map_article(item: dict) -> NewsArticle:
    content = item.get("content") or {}
    provider = content.get("provider") or {}
    link = (
        extract_url(content.get("canonicalUrl"))
        or extract_url(content.get("clickThroughUrl"))
        or text_or_none(content.get("url"))
    )
    return NewsArticle(
        id=text_or_none(item.get("id")),
        title=text_or_none(content.get("title")),
        summary=text_or_none(content.get("summary")) or text_or_none(content.get("description")),
        publisher=text_or_none(provider.get("displayName")) or text_or_none(content.get("publisher")),
        link=link,
        pubDate=text_or_none(content.get("pubDate")),
        type=text_or_none(content.get("contentType")),
        raw=item,
    )


def _map_search_news_item(item: dict) -> NewsArticle:
    pub_ts = item.get("providerPublishTime")
    pub_date: str | None = None
    if isinstance(pub_ts, (int, float)):
        from datetime import datetime, timezone

        pub_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc).isoformat()
    return NewsArticle(
        id=text_or_none(item.get("uuid")),
        title=text_or_none(item.get("title")),
        summary=text_or_none(item.get("summary")) or text_or_none(item.get("publisher")),
        publisher=text_or_none(item.get("publisher")),
        link=text_or_none(item.get("link")),
        pubDate=pub_date,
        type=text_or_none(item.get("type")),
        raw=item,
    )


def _fetch_via_yfinance(symbol: str, count: int, tab: str) -> list[NewsArticle]:
    news = yf.Ticker(symbol).get_news(count=count, tab=tab)
    return [_map_article(item) for item in news if not item.get("ad")]


def _fetch_via_yahoo_api(symbol: str, count: int, tab: str) -> list[NewsArticle]:
    query_ref = TAB_QUERY_REFS[tab]
    payload = json.dumps({"serviceConfig": {"snippetCount": count, "s": [symbol]}}).encode("utf-8")
    request = urllib.request.Request(
        YAHOO_NEWS_URL.format(query_ref=query_ref),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimitError("Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。") from exc
        raise RuntimeError(f"Yahoo API HTTP {exc.code}") from exc

    if "Will be right back" in body:
        raise RuntimeError("Yahoo Finance is temporarily unavailable")

    data = json.loads(body)
    stream = data.get("data", {}).get("tickerStream", {}).get("stream", [])
    return [_map_article(item) for item in stream if not item.get("ad")]


def _fetch_via_yfinance_search(query: str, count: int) -> list[NewsArticle]:
    search = yf.Search(query, news_count=count, include_research=False)
    news = search.news or []
    return [_map_search_news_item(item) for item in news if not item.get("ad")]


def _fetch_via_yahoo_search_api(query: str, count: int, *, region: str, lang: str) -> list[NewsArticle]:
    url = YAHOO_SEARCH_URL.format(
        query=urllib.parse.quote_plus(query),
        count=count,
        region=urllib.parse.quote_plus(region),
        lang=urllib.parse.quote_plus(lang),
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimitError("Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。") from exc
        raise RuntimeError(f"Yahoo Search HTTP {exc.code}") from exc

    if "Will be right back" in body:
        raise RuntimeError("Yahoo Finance is temporarily unavailable")

    data = json.loads(body)
    news_items = data.get("news") or []
    return [_map_search_news_item(item) for item in news_items if not item.get("ad")]


def get_news(ticker: str, count: int = 10, tab: str = "news") -> list[NewsArticle]:
    normalized_tab = tab.lower()
    if normalized_tab not in VALID_TABS:
        raise ValueError(f"Invalid tab '{tab}'. Choose from: {', '.join(sorted(VALID_TABS))}")

    symbol = ticker.strip().upper()
    if not symbol:
        raise ValueError("ticker cannot be empty")
    yfinance_rate_limited = False
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        if attempt > 0:
            time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
        try:
            return _fetch_via_yfinance(symbol, count, normalized_tab)
        except YFRateLimitError:
            yfinance_rate_limited = True

    if yfinance_rate_limited:
        try:
            return _fetch_via_yahoo_api(symbol, count, normalized_tab)
        except RateLimitError:
            raise
        except Exception as exc:
            raise RateLimitError(
                "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试，并避免在 Swagger 中连续点击 Execute。"
            ) from exc

    raise RateLimitError("Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。")


def get_market_news(count: int = 20, query: str | None = None, market: str = "us") -> list[NewsArticle]:
    if count < 1 or count > 200:
        raise ValueError("count must be between 1 and 200")

    _, region, lang = resolve_market_region(market)

    queries: tuple[str, ...] | list[str]
    if query:
        queries = [query]
    else:
        queries = DEFAULT_MARKET_QUERIES

    aggregated: dict[str, NewsArticle] = {}
    last_error: Exception | None = None
    yfinance_rate_limited = False
    per_query = max(5, count // max(1, len(queries)))

    for q in queries:
        articles: list[NewsArticle] | None = None
        for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
            if attempt > 0:
                time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
            try:
                articles = _fetch_via_yfinance_search(q, per_query)
                break
            except YFRateLimitError:
                yfinance_rate_limited = True
            except AttributeError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                break

        if articles is None:
            try:
                articles = _fetch_via_yahoo_search_api(q, per_query, region=region, lang=lang)
            except RateLimitError:
                raise
            except Exception as exc:
                last_error = exc
                continue

        for article in articles:
            key = article.id or article.link or article.title
            if key and key not in aggregated:
                aggregated[key] = article
                if len(aggregated) >= count:
                    break

        if len(aggregated) >= count:
            break

    if not aggregated:
        if last_error is not None:
            raise RuntimeError(f"Failed to fetch market news: {last_error}") from last_error
        if yfinance_rate_limited:
            raise RateLimitError("Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。")
        return []

    results = list(aggregated.values())
    results.sort(key=lambda article: article.pub_date or "", reverse=True)
    return results[:count]
