import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from app.models import NewsArticle
from app.services.article_content import fetch_article_content
from app.services.common import (
    CHINA_TZ,
    DEFAULT_MARKET_QUERIES,
    MARKET_TIME_RANGE_QUERIES,
    RateLimitError,
    dedup_articles,
)
from app.services.news_fetcher import get_market_news, get_news


def _parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    parsed_by_date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
    iso = text.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"无法解析时间 '{value}'，请使用 ISO8601 或 YYYY-MM-DD[ HH:MM[:SS]] 格式")

    if end_of_day and parsed_by_date_only:
        dt = dt + timedelta(days=1) - timedelta(microseconds=1)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    return dt.astimezone(timezone.utc)


def _article_datetime(article: NewsArticle) -> datetime | None:
    if not article.pub_date:
        return None

    text = article.pub_date.strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _matches_query(article: NewsArticle, query: str) -> bool:
    haystack = " ".join([
        article.title or "",
        article.summary or "",
        article.publisher or "",
        article.content or "",
    ]).lower()
    tokens = [token for token in query.lower().split() if token]
    if not tokens:
        return True
    return all(token in haystack for token in tokens)


def _gather_market_news_for_time_range(*, target_count: int) -> list[NewsArticle]:
    queries = [
        *DEFAULT_MARKET_QUERIES,
        *MARKET_TIME_RANGE_QUERIES,
    ]
    aggregated: list[NewsArticle] = []
    per_query = min(200, max(20, target_count // max(1, len(queries)) + 8))

    for query in queries:
        try:
            batch = get_market_news(count=per_query, query=query)
        except Exception:
            continue
        aggregated.extend(batch)

    return dedup_articles(aggregated)


def search_news(
    *,
    ticker: str | None = None,
    query: str | None = None,
    since: str | None = None,
    until: str | None = None,
    count: int = 20,
    tab: str = "news",
    with_content: bool = False,
    max_articles: int = 5,
) -> list[NewsArticle]:
    if count < 1 or count > 200:
        raise ValueError("count must be between 1 and 200")

    since_dt = _parse_datetime(since)
    until_dt = _parse_datetime(until, end_of_day=True)
    if since_dt and until_dt and since_dt > until_dt:
        raise ValueError("since 不能晚于 until")

    fetch_count = min(200, max(count * 3, count + 20)) if (since_dt or until_dt) else count

    if ticker:
        articles = get_news(ticker, count=fetch_count, tab=tab)
    elif query:
        articles = get_market_news(count=fetch_count, query=query)
    else:
        if since_dt or until_dt:
            articles = _gather_market_news_for_time_range(target_count=max(fetch_count, count * 6))
        else:
            articles = get_market_news(count=fetch_count)

    if query:
        articles = [article for article in articles if _matches_query(article, query)]

    if since_dt or until_dt:
        filtered: list[NewsArticle] = []
        for article in articles:
            dt = _article_datetime(article)
            if dt is None:
                continue
            if since_dt and dt < since_dt:
                continue
            if until_dt and dt > until_dt:
                continue
            filtered.append(article)
        articles = filtered

    articles.sort(key=lambda article: article.pub_date or "", reverse=True)
    articles = articles[:count]

    if with_content and articles:
        enrich_count = max(0, min(max_articles, len(articles)))
        for article in articles[:enrich_count]:
            if not article.link:
                continue
            try:
                article.content = fetch_article_content(article.link)
            except RateLimitError:
                break
            except Exception:
                continue

    return articles
