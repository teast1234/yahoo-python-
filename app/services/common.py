from datetime import timedelta, timezone

from app.models import NewsArticle

VALID_TABS = {"news", "all", "press releases"}
RETRY_DELAYS_SECONDS = (3, 8, 15)
TAB_QUERY_REFS = {
    "all": "newsAll",
    "news": "latestNews",
    "press releases": "pressRelease",
}
YAHOO_NEWS_URL = "https://finance.yahoo.com/xhr/ncp?queryRef={query_ref}&serviceKey=ncp_fin"
YAHOO_SEARCH_URL = (
    "https://query1.finance.yahoo.com/v1/finance/search"
    "?q={query}&newsCount={count}&quotesCount=0&lang=en-US&region=US"
)
DEFAULT_MARKET_QUERIES = ("stock market", "finance", "economy")
MARKET_TIME_RANGE_QUERIES = (
    "wall street",
    "nasdaq",
    "dow jones",
    "s&p 500",
    "earnings",
    "inflation",
    "federal reserve",
    "bond yield",
    "oil price",
    "gold price",
    "ai stocks",
    "spacex",
)
CHINA_TZ = timezone(timedelta(hours=8))


class RateLimitError(Exception):
    pass


def text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_url(value: object) -> str | None:
    if isinstance(value, dict):
        return text_or_none(value.get("url"))
    return text_or_none(value)


def dedup_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    deduped: dict[str, NewsArticle] = {}
    for article in articles:
        key = article.id or article.link or article.title
        if not key:
            continue
        if key in deduped:
            continue
        deduped[key] = article
    return list(deduped.values())
