from fastapi import FastAPI, HTTPException, Query

from app.models import NewsResponse
from app.news_service import (
    RateLimitError,
    fetch_article_content,
    get_market_news,
    get_news,
    get_news_with_content,
    search_news,
)

app = FastAPI(
    title="Yahoo Finance News",
    description="Fetch Yahoo Finance news via yfinance",
    version="1.0.0",
)


@app.get("/")
def root() -> dict:
    return {
        "name": "Yahoo Finance News",
        "docs": "/docs",
        "examples": [
            "/api/search?ticker=AAPL&count=5",
            "/api/search?query=AI&count=10",
            "/api/search?ticker=AAPL&query=iphone",
            "/api/search?since=2026-06-01&until=2026-06-23",
            "/api/search?count=20",
            "/api/news/AAPL?count=5&tab=news",
            "/api/news?count=20",
            "/api/article?link=https://finance.yahoo.com/m/<uuid>/...",
        ],
    }


@app.get("/api/news", response_model=NewsResponse)
def fetch_market_news(
    count: int = Query(default=20, ge=1, le=200),
    query: str | None = Query(
        default=None,
        description="可选搜索关键词。留空时聚合大盘头条（stock market / finance / economy）",
    ),
    with_content: bool = Query(
        default=False,
        description="是否抓取每条新闻的正文（会显著变慢，建议配合较小的 count）",
    ),
    max_articles: int = Query(
        default=5,
        ge=1,
        le=20,
        description="启用 with_content 时最多抓取正文的条数",
    ),
) -> NewsResponse:
    try:
        if with_content:
            articles = get_news_with_content(
                market=True,
                count=count,
                query=query,
                max_articles=max_articles,
            )
        else:
            articles = get_market_news(count=count, query=query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch news: {exc}") from exc

    return NewsResponse(
        ticker="MARKET",
        tab=query or "headlines",
        count=len(articles),
        articles=articles,
    )


@app.get("/api/news/{ticker}", response_model=NewsResponse)
def fetch_news(
    ticker: str,
    count: int = Query(default=10, ge=1, le=200),
    tab: str = Query(default="news"),
    with_content: bool = Query(
        default=False,
        description="是否抓取每条新闻的正文",
    ),
    max_articles: int = Query(
        default=5,
        ge=1,
        le=20,
        description="启用 with_content 时最多抓取正文的条数",
    ),
) -> NewsResponse:
    try:
        if with_content:
            articles = get_news_with_content(
                ticker=ticker,
                count=count,
                tab=tab,
                market=False,
                max_articles=max_articles,
            )
        else:
            articles = get_news(ticker, count=count, tab=tab)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch news: {exc}") from exc

    return NewsResponse(
        ticker=ticker.upper(),
        tab=tab.lower(),
        count=len(articles),
        articles=articles,
    )


@app.get("/api/article")
def fetch_article(
    link: str = Query(..., description="Yahoo Finance 新闻文章链接"),
) -> dict:
    try:
        content = fetch_article_content(link)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch article: {exc}") from exc

    return {"link": link, "content": content, "length": len(content)}


@app.get("/api/search", response_model=NewsResponse)
def search(
    ticker: str | None = Query(default=None, description="股票代码，例如 AAPL"),
    query: str | None = Query(default=None, description="关键词模糊搜索；与 ticker 可同时使用"),
    since: str | None = Query(
        default=None,
        description="起始时间，支持 YYYY-MM-DD / YYYY-MM-DD HH:MM / ISO8601",
    ),
    until: str | None = Query(
        default=None,
        description="截止时间，支持 YYYY-MM-DD / YYYY-MM-DD HH:MM / ISO8601",
    ),
    count: int = Query(default=20, ge=1, le=200),
    tab: str = Query(default="news", description="仅 ticker 模式生效：news / all / press releases"),
    with_content: bool = Query(default=False, description="是否抓取每条新闻的正文"),
    max_articles: int = Query(default=5, ge=1, le=20),
) -> NewsResponse:
    try:
        articles = search_news(
            ticker=ticker,
            query=query,
            since=since,
            until=until,
            count=count,
            tab=tab,
            with_content=with_content,
            max_articles=max_articles,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to search news: {exc}") from exc

    if ticker:
        resp_ticker = ticker.upper()
        resp_tab = tab.lower() if not query else f"{tab.lower()}|q={query}"
    elif query:
        resp_ticker = "SEARCH"
        resp_tab = f"q={query}"
    else:
        resp_ticker = "MARKET"
        resp_tab = "headlines"

    return NewsResponse(
        ticker=resp_ticker,
        tab=resp_tab,
        count=len(articles),
        articles=articles,
    )
