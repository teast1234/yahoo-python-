import argparse
import json

import uvicorn

from app.models import NewsResponse
from app.news_service import VALID_TABS, RateLimitError, fetch_article_content, search_news


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="Fetch Yahoo Finance news via yfinance")
    parser.add_argument("ticker", nargs="?", help="Stock ticker, e.g. AAPL")
    parser.add_argument("--count", type=int, default=10, help="Number of articles (1-200)")
    parser.add_argument(
        "--tab",
        default="news",
        choices=sorted(VALID_TABS),
        help="News tab type",
    )
    parser.add_argument("--serve", action="store_true", help="Start HTTP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--market",
        action="store_true",
        help="抓取每日雅虎财经头条（不依赖具体股票代码）",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="搭配 --market 使用的搜索关键词，例如 --query AI",
    )
    parser.add_argument(
        "--with-content",
        dest="with_content",
        action="store_true",
        help="抓取每条新闻的正文（速度较慢）",
    )
    parser.add_argument(
        "--max-articles",
        dest="max_articles",
        type=int,
        default=5,
        help="启用 --with-content 时最多抓取正文的条数（1-20，默认 5）",
    )
    parser.add_argument(
        "--article",
        default=None,
        help="抓取指定 Yahoo Finance 文章 URL 的正文",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="起始时间（YYYY-MM-DD / YYYY-MM-DD HH:MM / ISO8601）",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="截止时间（YYYY-MM-DD / YYYY-MM-DD HH:MM / ISO8601）",
    )

    args = parser.parse_args()

    if args.serve:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
        return

    if args.article:
        try:
            content = fetch_article_content(args.article)
        except RateLimitError as exc:
            parser.error(str(exc))
        except Exception as exc:
            parser.error(f"Failed to fetch article: {exc}")
        print(json.dumps({"link": args.article, "content": content, "length": len(content)}, ensure_ascii=False, indent=2))
        return

    query = args.query
    if args.market and not query:
        query = None

    try:
        articles = search_news(
            ticker=args.ticker,
            query=query,
            since=args.since,
            until=args.until,
            count=args.count,
            tab=args.tab,
            with_content=args.with_content,
            max_articles=args.max_articles,
        )
    except ValueError as exc:
        parser.error(str(exc))
    except RateLimitError as exc:
        parser.error(str(exc))
    except Exception as exc:
        parser.error(f"Failed to search news: {exc}")

    if args.ticker:
        resp_ticker = args.ticker.upper()
        resp_tab = args.tab.lower() if not query else f"{args.tab.lower()}|q={query}"
    elif query:
        resp_ticker = "SEARCH"
        resp_tab = f"q={query}"
    else:
        resp_ticker = "MARKET"
        resp_tab = "headlines"

    payload = NewsResponse(
        ticker=resp_ticker,
        tab=resp_tab,
        count=len(articles),
        articles=articles,
    )
    print(json.dumps(payload.model_dump(by_alias=True), ensure_ascii=False, indent=2))
