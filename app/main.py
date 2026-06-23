# ============================================================
# main.py —— 应用入口（HTTP Controller + CLI 命令行）
# ------------------------------------------------------------
# Java 类比：
#   1) HTTP 部分相当于 Spring Boot 的 @RestController + 启动类（main 方法）
#   2) CLI 部分相当于一个独立的命令行工具类（带 main 方法的 Java 程序）
# 这两种形态共用同一个 Service 层（app/news_service.py）。
# ============================================================

# ---- 标准库 ----
import argparse  # 解析命令行参数（类似 Java 的 Apache Commons CLI / picocli）
import json      # JSON 序列化（类似 Jackson）

# ---- 第三方库 ----
import uvicorn   # ASGI 服务器，用来跑 FastAPI（类似 Tomcat / Netty 之于 Spring）
from fastapi import FastAPI, HTTPException, Query
# FastAPI       —— Web 框架本体（类似 Spring Boot Web）
# HTTPException —— 框架自带的异常类，抛出后会被自动转成 HTTP 错误响应
# Query         —— 用来声明 query 参数及其校验规则（类似 Spring 的 @RequestParam）

# ---- 本项目内部模块 ----
from app.models import NewsResponse
from app.news_service import (
    VALID_TABS,
    RateLimitError,
    fetch_article_content,
    get_market_news,
    get_news,
    get_news_with_content,
    search_news,
)


# ============== 创建 FastAPI 应用实例 ==============
# 等价于 Java 的：
#   SpringApplication app = new SpringApplication(...);
# 但 FastAPI 是「实例化对象 + 装饰器注册路由」的方式，更接近 Express / Flask 的写法。
app = FastAPI(
    title="Yahoo Finance News",
    description="Fetch Yahoo Finance news via yfinance",
    version="1.0.0",
)


# ============== 路由 1：根路径 ==============
# `@app.get("/")` 是 Python 的「装饰器」语法，类似 Java 注解 @GetMapping("/")。
# 装饰器作用：把下面这个函数注册为「GET / 」的处理器。
@app.get("/")
def root() -> dict:
    """
    根路径，返回简单的服务说明。
    返回值是 dict，FastAPI 会自动把它序列化成 JSON 响应（类似 Spring 自动 Jackson 序列化）。
    """
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


# ============== 路由 2：每日财经头条 ==============
# 不绑定具体 ticker，用关键词搜索抓取 Yahoo Finance 全站头条。
# 注意：这个路由必须放在 `/api/news/{ticker}` 之前，
# 避免 FastAPI 把 `/api/news` 当成 ticker 为空的路径匹配（虽然实际不会，但显式更安全）。
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
    """
    获取每日最新雅虎财经新闻（不依赖具体股票代码）。
    Java 类比：
        @GetMapping("/api/news")
        public NewsResponse fetchMarketNews(
            @RequestParam(defaultValue="20") @Min(1) @Max(200) int count,
            @RequestParam(required=false) String query,
            @RequestParam(defaultValue="false") boolean withContent,
            @RequestParam(defaultValue="5") int maxArticles) { ... }
    """
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
        ticker="MARKET",            # 用 MARKET 表示「整体大盘」
        tab=query or "headlines",   # 没传 query 就标记为 headlines
        count=len(articles),
        articles=articles,
    )


# ============== 路由 3：按股票代码获取新闻 ==============
# `{ticker}` 是路径参数（path variable），类似 Spring 的 @PathVariable。
# `response_model=NewsResponse` 让 FastAPI 自动按照该模型校验并序列化响应体。
@app.get("/api/news/{ticker}", response_model=NewsResponse)
def fetch_news(
    ticker: str,                                       # 路径参数：股票代码
    count: int = Query(default=10, ge=1, le=200),     # query 参数：默认 10，范围 [1,200]
    tab: str = Query(default="news"),                 # query 参数：tab 类型，默认 news
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
    """
    根据股票代码获取新闻列表。
    Java 类比：
        @GetMapping("/api/news/{ticker}")
        public NewsResponse fetchNews(
            @PathVariable String ticker,
            @RequestParam(defaultValue="10") @Min(1) @Max(200) int count,
            @RequestParam(defaultValue="news") String tab) { ... }
    """
    # 异常处理：把 Service 层的异常映射成对应的 HTTP 状态码
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
        # 参数非法 → 400 Bad Request
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitError as exc:
        # 上游限流 → 429 Too Many Requests
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        # 其他未知异常 → 502 Bad Gateway
        raise HTTPException(status_code=502, detail=f"Failed to fetch news: {exc}") from exc

    # 用 Pydantic 模型构造响应（FastAPI 会按 response_model 进行校验和序列化）
    return NewsResponse(
        ticker=ticker.upper(),
        tab=tab.lower(),
        count=len(articles),       # len(list) 类似 Java 的 list.size()
        articles=articles,
    )


# ============== 路由 4：抓取单篇文章正文 ==============
# 用法示例：
#   GET /api/article?link=https://finance.yahoo.com/m/<uuid>/xxx.html
@app.get("/api/article")
def fetch_article(
    link: str = Query(..., description="Yahoo Finance 新闻文章链接"),
) -> dict:
    """
    给定一篇 Yahoo Finance 新闻 URL，返回其正文纯文本。
    Java 类比：
        @GetMapping("/api/article")
        public Map<String, String> fetchArticle(@RequestParam String link) { ... }
    """
    try:
        content = fetch_article_content(link)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch article: {exc}") from exc

    return {"link": link, "content": content, "length": len(content)}


# ============== 路由 5：统一搜索接口（推荐使用） ==============
# 把【股票代码 / 关键词 / 时间区间 / 每日头条】合并为一个查询入口。
# 不传任何条件时，自动返回最新的财经头条。
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
    """
    统一新闻查询接口。

    | ticker  | query   | 行为                                            |
    |---------|---------|-------------------------------------------------|
    | 有      | 空      | 按股票代码抓新闻                                |
    | 有      | 有      | 股票代码新闻 → 用 query 在标题/摘要/正文中过滤  |
    | 空      | 有      | 关键词搜索全站新闻                              |
    | 空      | 空      | 抓每日财经头条                                  |

    时间过滤：since / until 可单传或同时传；不传则返回最新新闻。
    """
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

    # 构造一个「描述本次查询」的 ticker / tab 标记，方便前端展示
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


# ============== CLI 入口 ==============
def run_cli() -> None:
    """
    命令行入口函数。
    Java 类比：public static void main(String[] args) { ... }
    Python 用 argparse 来解析命令行参数。
    """
    # ---------- 1. 定义参数 ----------
    parser = argparse.ArgumentParser(description="Fetch Yahoo Finance news via yfinance")

    # 位置参数（positional）：股票代码
    # nargs="?" 表示该参数可选（0 或 1 个）
    parser.add_argument("ticker", nargs="?", help="Stock ticker, e.g. AAPL")

    # 可选参数（前面带 `--`，类似 Java 命令行的 -Dxxx=yyy）
    parser.add_argument("--count", type=int, default=10, help="Number of articles (1-200)")
    parser.add_argument(
        "--tab",
        default="news",
        choices=sorted(VALID_TABS),  # 限定可选值，类似枚举
        help="News tab type",
    )
    # action="store_true" 表示这是一个布尔开关，写了就为 True
    parser.add_argument("--serve", action="store_true", help="Start HTTP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)

    # ---- 每日财经头条相关参数 ----
    # --market 开关：开启后忽略 ticker，抓取雅虎财经全站头条
    parser.add_argument(
        "--market",
        action="store_true",
        help="抓取每日雅虎财经头条（不依赖具体股票代码）",
    )
    # --query 可选关键词，配合 --market 使用；不传则聚合大盘默认关键词
    parser.add_argument(
        "--query",
        default=None,
        help="搭配 --market 使用的搜索关键词，例如 --query AI",
    )

    # ---- 文章正文相关参数 ----
    # --with-content：抓列表 + 正文。会发起额外 HTTP 请求，所以默认关闭。
    parser.add_argument(
        "--with-content",
        dest="with_content",
        action="store_true",
        help="抓取每条新闻的正文（速度较慢）",
    )
    # --max-articles：with-content 时最多抓取正文的条数
    parser.add_argument(
        "--max-articles",
        dest="max_articles",
        type=int,
        default=5,
        help="启用 --with-content 时最多抓取正文的条数（1-20，默认 5）",
    )
    # --article：直接抓取一篇指定 URL 的文章正文
    parser.add_argument(
        "--article",
        default=None,
        help="抓取指定 Yahoo Finance 文章 URL 的正文",
    )

    # ---- 统一搜索时间过滤参数 ----
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

    # ---------- 2. 解析命令行 ----------
    # args 是一个对象，args.xxx 即可访问到刚才定义的参数
    args = parser.parse_args()

    # ---------- 3. 模式分支：HTTP 服务 还是 一次性查询 ----------
    if args.serve:
        # 启动 uvicorn 服务，加载 "app.main:app" 这个 FastAPI 应用对象
        # 字符串格式 "模块路径:对象名" 是 ASGI 标准的应用引用方式
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
        return

    # ---------- 4a. 直接抓单篇文章正文（--article URL） ----------
    if args.article:
        try:
            content = fetch_article_content(args.article)
        except RateLimitError as exc:
            parser.error(str(exc))
        except Exception as exc:
            parser.error(f"Failed to fetch article: {exc}")
        print(json.dumps(
            {"link": args.article, "content": content, "length": len(content)},
            ensure_ascii=False, indent=2,
        ))
        return

    # ---------- 4b. 统一搜索模式 ----------
    # 任意条件（ticker / --query / --since / --until / --market）都进入这里。
    # 没传任何条件时也走该分支：等价于「拉取最新财经头条」。
    try:
        articles = search_news(
            ticker=args.ticker,
            query=args.query,
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

    # 构造响应里的 ticker / tab 标记
    if args.ticker:
        resp_ticker = args.ticker.upper()
        resp_tab = args.tab.lower() if not args.query else f"{args.tab.lower()}|q={args.query}"
    elif args.query:
        resp_ticker = "SEARCH"
        resp_tab = f"q={args.query}"
    else:
        resp_ticker = "MARKET"
        resp_tab = "headlines"

    # ---------- 5. 打印 JSON ----------
    payload = NewsResponse(
        ticker=resp_ticker,
        tab=resp_tab,
        count=len(articles),
        articles=articles,
    )
    # model_dump(by_alias=True)：把模型转成 dict，并使用 alias 名（如 pubDate）
    # ensure_ascii=False：不要把中文转义成 \uXXXX
    # indent=2：美化输出
    print(json.dumps(payload.model_dump(by_alias=True), ensure_ascii=False, indent=2))


# ============== 程序入口 ==============
# 这一段是 Python 的标准入口写法。
# `__name__` 是模块的内置变量：
#   - 当这个文件被「直接运行」(python -m app.main) 时，__name__ == "__main__"
#   - 当它被其他文件 import 时，__name__ 是模块名 "app.main"
# Java 类比：
#   public static void main(String[] args) 的判断条件，
#   只有「直接运行」才会进入 run_cli()。
if __name__ == "__main__":
    run_cli()
