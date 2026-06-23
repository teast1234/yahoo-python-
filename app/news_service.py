# ============================================================
# news_service.py —— 业务服务层（Service Layer）
# ------------------------------------------------------------
# Java 类比：相当于 Spring Boot 中的 @Service 类，
# 负责调用第三方库 / HTTP 接口，做数据抓取、转换、容错降级。
# ============================================================

# Python 的 import 类似 Java 的 import；
# 标准库无需安装，直接 import 即可使用。
import html               # HTML 实体反转义（&amp; → &），类似 Java 的 StringEscapeUtils.unescapeHtml4
import json                # JSON 序列化/反序列化（类似 Jackson 的 ObjectMapper）
import re                  # 正则表达式（类似 Java 的 java.util.regex）
import time                # 提供 sleep 等时间相关函数
import urllib.error        # urllib 的异常定义
import urllib.parse        # URL 编码工具（类似 Java 的 URLEncoder）
import urllib.request      # urllib 是 Python 内置的 HTTP 客户端（类似 Java 的 HttpURLConnection）
from datetime import datetime, timedelta, timezone  # 时间戳与 ISO8601 转换（类似 Java 的 Instant）
from email.utils import parsedate_to_datetime

# 第三方库 yfinance：封装了 Yahoo Finance 的数据抓取
import yfinance as yf      # `as yf` 是给模块起别名（类似 Java 没有，但相当于 import 简写）
from yfinance.exceptions import YFRateLimitError  # 限流异常类型

# 从同项目下的 app/models.py 导入 NewsArticle 类
from app.models import NewsArticle


# ============== 模块级常量（相当于 Java 的 public static final） ==============

# 合法的新闻 tab 类型集合
# `{...}` 是 Python 的 set（集合），类似 Java 的 Set<String>
VALID_TABS = {"news", "all", "press releases"}

# 重试间隔时间（秒），元组 tuple 是不可变列表，类似 Java 的 List.of(3, 8, 15)
RETRY_DELAYS_SECONDS = (3, 8, 15)

# tab 名称 → Yahoo 内部 queryRef 的映射
# `{...:...}` 是字典 dict，类似 Java 的 Map<String, String>
TAB_QUERY_REFS = {
    "all": "newsAll",
    "news": "latestNews",
    "press releases": "pressRelease",
}

# Yahoo 内部新闻接口 URL 模板。{query_ref} 是占位符，后面会用 .format() 替换。
YAHOO_NEWS_URL = "https://finance.yahoo.com/xhr/ncp?queryRef={query_ref}&serviceKey=ncp_fin"

# Yahoo 全站搜索接口（不需要绑定到具体 ticker，用来抓「每日财经头条」）
YAHOO_SEARCH_URL = (
    "https://query1.finance.yahoo.com/v1/finance/search"
    "?q={query}&newsCount={count}&quotesCount=0&lang=en-US&region=US"
)

# 抓「每日头条」时使用的默认搜索关键词
# Yahoo Search 接口要求必须有 q 参数，这里用通用关键词来获取大盘新闻
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

# 中国标准时间（UTC+8）：当用户输入 since/until 未显式带时区时，按该时区解释
CHINA_TZ = timezone(timedelta(hours=8))


# ============== 自定义异常 ==============
class RateLimitError(Exception):
    """
    自定义异常：表示 Yahoo Finance 返回限流。
    Java 类比：public class RateLimitError extends RuntimeException { ... }

    类下面三个引号包裹的字符串叫 docstring，
    相当于 Java 的 Javadoc 注释，IDE 会显示在悬浮提示中。
    """


# ============== 私有工具函数（按 Python 约定，下划线开头表示「私有」） ==============

def _text(value: object) -> str | None:
    """
    把任意对象转换为「非空字符串」或 None。
    - 入参 value：任意类型（object 类似 Java 的 Object）
    - 返回值：str（非空字符串）或 None
    Java 类比：StringUtils.trimToNull(String.valueOf(value))
    """
    if value is None:
        return None
    # str(value) 类似 Java 的 String.valueOf(value)
    # .strip() 类似 Java 的 String.trim()
    text = str(value).strip()
    # Python 中空字符串 "" 在布尔上下文里等价于 false，
    # 所以这里写 `text or None` 等价于 Java 的：text.isEmpty() ? null : text
    return text or None


def _extract_url(value: object) -> str | None:
    """
    从可能是 dict（含 url 字段）或字符串的对象中提取 URL。
    Yahoo 接口里链接字段格式不统一，所以需要兼容。
    """
    # isinstance(x, dict) 类似 Java 的 x instanceof Map
    if isinstance(value, dict):
        # dict.get(key) 类似 Java 的 map.get(key)，没有就返回 None
        return _text(value.get("url"))
    return _text(value)


def _map_article(item: dict) -> NewsArticle:
    """
    将 Yahoo 返回的原始 JSON（dict）转换为 NewsArticle 对象。
    Java 类比：一个 Converter / Mapper 方法，把 Map<String, Object> -> NewsArticle DTO。
    """
    # `item.get("content") or {}` 是惯用写法：
    # 如果 content 为 None，就用空 dict，避免后续 .get() 报错。
    content = item.get("content") or {}
    provider = content.get("provider") or {}

    # 链接字段优先级：canonicalUrl > clickThroughUrl > url
    # 这种写法相当于 Java 的多重三元表达式：
    # link = a != null ? a : (b != null ? b : c);
    link = (
        _extract_url(content.get("canonicalUrl"))
        or _extract_url(content.get("clickThroughUrl"))
        or _text(content.get("url"))
    )

    # 构造 Pydantic 模型，等价于 Java 中 new NewsArticle(...)
    # 这里直接用 关键字参数 传值，类似 Lombok 的 @Builder
    return NewsArticle(
        id=_text(item.get("id")),
        title=_text(content.get("title")),
        summary=_text(content.get("summary")) or _text(content.get("description")),
        publisher=_text(provider.get("displayName")) or _text(content.get("publisher")),
        link=link,
        pubDate=_text(content.get("pubDate")),
        type=_text(content.get("contentType")),
        raw=item,
    )


# ============== 两条获取新闻的通道（主路径 + 降级路径） ==============

def _fetch_via_yfinance(symbol: str, count: int, tab: str) -> list[NewsArticle]:
    """
    主路径：使用 yfinance 库获取新闻。
    Java 类比：调用 SDK 的 client.getNews(...)
    """
    # yf.Ticker(symbol) 创建一个股票对象
    # .get_news(...) 拉取该股票的新闻
    news = yf.Ticker(symbol).get_news(count=count, tab=tab)

    # 列表推导式（list comprehension）：
    # [表达式 for 元素 in 序列 if 条件]
    # 这一行等价于 Java 的：
    #   news.stream()
    #       .filter(item -> !Boolean.TRUE.equals(item.get("ad")))
    #       .map(this::mapArticle)
    #       .collect(Collectors.toList());
    return [_map_article(item) for item in news if not item.get("ad")]


def _fetch_via_yahoo_api(symbol: str, count: int, tab: str) -> list[NewsArticle]:
    """
    降级路径：当 yfinance 被限流时，绕过它直接调用 Yahoo 的内部 XHR 接口。
    Java 类比：用 HttpURLConnection / OkHttp 自己发 POST 请求。
    """
    # 根据 tab 取对应的 queryRef 参数
    query_ref = TAB_QUERY_REFS[tab]

    # 构造请求体：先构造 dict → json 序列化 → encode 成字节
    # encode("utf-8") 相当于 Java 的 string.getBytes(StandardCharsets.UTF_8)
    payload = json.dumps(
        {"serviceConfig": {"snippetCount": count, "s": [symbol]}}
    ).encode("utf-8")

    # 构造 HTTP 请求对象（类似 Java new HttpPost(url) + setEntity + setHeaders）
    request = urllib.request.Request(
        YAHOO_NEWS_URL.format(query_ref=query_ref),  # URL 模板替换
        data=payload,                                 # 请求体
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # 伪装成浏览器，避免被识别为爬虫
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="POST",
    )

    # try/except 是 Python 的异常处理，对应 Java 的 try/catch
    try:
        # urlopen(...) 发起请求，with 语法等于 Java 的 try-with-resources，
        # 退出时会自动关闭 response 资源。
        with urllib.request.urlopen(request, timeout=30) as response:
            # response.read() 读取字节流，再 decode 成字符串
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # 捕获 HTTP 错误，`as exc` 类似 Java 的 catch (HTTPError exc)
        if exc.code == 429:
            # 抛出我们自定义的限流异常
            # `from exc` 用于保留原始异常链，类似 Java 的 throw new XXX(msg, cause)
            raise RateLimitError(
                "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。"
            ) from exc
        raise RuntimeError(f"Yahoo API HTTP {exc.code}") from exc

    # Yahoo 维护中会返回这一段提示
    if "Will be right back" in body:
        raise RuntimeError("Yahoo Finance is temporarily unavailable")

    # 反序列化 JSON
    data = json.loads(body)

    # 链式 .get(...) 可以安全访问嵌套字段（任一层为 None 就返回兜底值）
    # 类似 Java 的 Optional.ofNullable(map).map(m -> m.get("data"))...
    stream = data.get("data", {}).get("tickerStream", {}).get("stream", [])
    return [_map_article(item) for item in stream if not item.get("ad")]


# ============== 对外暴露的核心方法 ==============

def get_news(ticker: str, count: int = 10, tab: str = "news") -> list[NewsArticle]:
    """
    获取指定股票代码的新闻列表（带容错与降级）。

    参数：
        ticker: 股票代码，如 "AAPL"
        count : 数量，默认 10
        tab   : 新闻类型，默认 "news"
    返回：
        NewsArticle 列表

    说明：
        Python 中函数参数可以有「默认值」，类似 Java 的方法重载。
        函数签名末尾的 `-> list[NewsArticle]` 是返回值类型注解。
    """
    # 把 tab 统一转小写，避免大小写不一致导致校验失败
    normalized_tab = tab.lower()
    if normalized_tab not in VALID_TABS:
        # 抛出 ValueError，对应「参数非法」（在 main.py 中会被映射成 HTTP 400）
        # f"..." 是 f-string 格式化字符串，类似 Java 的 String.format / 文本块拼接
        raise ValueError(
            f"Invalid tab '{tab}'. Choose from: {', '.join(sorted(VALID_TABS))}"
        )

    symbol = ticker.upper()
    yfinance_rate_limited = False  # 标记位：yfinance 是否触发了限流

    # ---------- 第一阶段：yfinance 主路径 + 重试 ----------
    # range(N) 生成 0..N-1 的序列，类似 Java 的 for(int i=0; i<N; i++)
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        # 第一次尝试不 sleep，从第二次起按延迟数组退避
        if attempt > 0:
            time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])
        try:
            # 成功拿到结果直接 return，类似 Java 中的 return
            return _fetch_via_yfinance(symbol, count, normalized_tab)
        except YFRateLimitError:
            # 仅在限流时继续重试
            yfinance_rate_limited = True

    # ---------- 第二阶段：所有重试都失败 → 切换到降级通道 ----------
    if yfinance_rate_limited:
        try:
            return _fetch_via_yahoo_api(symbol, count, normalized_tab)
        except RateLimitError:
            # 降级通道也限流：直接把异常往外抛
            raise
        except Exception as exc:
            # 其他异常统一包装成 RateLimitError，提示用户稍后再试
            raise RateLimitError(
                "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试，"
                "并避免在 Swagger 中连续点击 Execute。"
            ) from exc

    # 兜底：理论上走不到这里
    raise RateLimitError(
        "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。"
    )


# ============================================================
# 以下为「每日财经新闻」相关函数：不绑定具体 ticker，
# 而是抓取 Yahoo Finance 全站的财经头条。
# ============================================================

def _map_search_news_item(item: dict) -> NewsArticle:
    """
    将 Yahoo Search 接口返回的新闻条目转换为 NewsArticle。
    Search 接口的字段结构与 Ticker 新闻接口不一样，所以单独写一个 mapper。
    Java 类比：另一个 Converter 方法。
    """
    # providerPublishTime 是 Unix 秒级时间戳（int），需要转成 ISO8601 字符串
    pub_ts = item.get("providerPublishTime")
    pub_date: str | None = None
    if isinstance(pub_ts, (int, float)):
        # datetime.fromtimestamp(...) 类似 Java 的 Instant.ofEpochSecond(ts)
        pub_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc).isoformat()

    return NewsArticle(
        id=_text(item.get("uuid")),
        title=_text(item.get("title")),
        # Search 接口往往没有 summary 字段，做兜底处理
        summary=_text(item.get("summary")) or _text(item.get("publisher")),
        publisher=_text(item.get("publisher")),
        link=_text(item.get("link")),
        pubDate=pub_date,
        type=_text(item.get("type")),
        raw=item,
    )


def _fetch_via_yfinance_search(query: str, count: int) -> list[NewsArticle]:
    """
    主路径：使用 yfinance 0.2.40+ 提供的 Search 类抓取新闻。
    yf.Search(query, news_count=N).news 会返回该关键词下的最新新闻列表。
    """
    # 部分老版本 yfinance 没有 Search，import 失败时由调用方走降级
    search = yf.Search(query, news_count=count, include_research=False)
    news = search.news or []
    return [_map_search_news_item(item) for item in news if not item.get("ad")]


def _fetch_via_yahoo_search_api(query: str, count: int) -> list[NewsArticle]:
    """
    降级路径：直接 HTTP GET Yahoo 的 search 接口。
    Java 类比：用 HttpURLConnection 发 GET 请求并解析 JSON。
    """
    url = YAHOO_SEARCH_URL.format(
        query=urllib.parse.quote_plus(query),  # URL 编码（空格→+）
        count=count,
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
            raise RateLimitError(
                "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。"
            ) from exc
        raise RuntimeError(f"Yahoo Search HTTP {exc.code}") from exc

    if "Will be right back" in body:
        raise RuntimeError("Yahoo Finance is temporarily unavailable")

    data = json.loads(body)
    news_items = data.get("news") or []
    return [_map_search_news_item(item) for item in news_items if not item.get("ad")]


def get_market_news(count: int = 20, query: str | None = None) -> list[NewsArticle]:
    """
    获取「每日最新雅虎财经新闻」，不绑定具体股票代码。

    参数：
        count: 期望返回的新闻条数（默认 20）
        query: 搜索关键词；为空时使用默认大盘关键词聚合多个分类

    返回：
        去重后的 NewsArticle 列表（按发布时间倒序，最多 count 条）

    实现策略：
        1) 主路径：yfinance 自带的 Search（基于关键词搜索）
        2) 降级路径：直接调用 Yahoo Search HTTP 接口
        3) query 为空时，循环多个默认关键词聚合，得到「财经头条」
    """
    if count < 1 or count > 200:
        # 参数校验：与 fetch_news 保持一致的范围
        raise ValueError("count must be between 1 and 200")

    # 选择查询词列表。tuple/list 都可以迭代。
    queries: tuple[str, ...] | list[str]
    if query:
        queries = [query]
    else:
        queries = DEFAULT_MARKET_QUERIES

    # 用 dict 来按 id/link 做去重（Python 的 dict 是有序的，等价于 LinkedHashMap）
    aggregated: dict[str, NewsArticle] = {}
    last_error: Exception | None = None
    yfinance_rate_limited = False

    # 单个关键词目标条数（按聚合关键词数平均分配，至少 5 条）
    per_query = max(5, count // max(1, len(queries)))

    for q in queries:
        # ---------- 第一阶段：yfinance Search 主路径 + 重试 ----------
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
                # 老版本 yfinance 没有 Search 类
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                break

        # ---------- 第二阶段：降级到原生 HTTP 搜索 ----------
        if articles is None:
            try:
                articles = _fetch_via_yahoo_search_api(q, per_query)
            except RateLimitError:
                # 整个接口都被限流时直接抛出
                raise
            except Exception as exc:
                last_error = exc
                continue  # 这个关键词失败就跳过，继续下一个

        # 把结果合并进 aggregated（按 id 或 link 去重）
        for art in articles:
            key = art.id or art.link or art.title
            if key and key not in aggregated:
                aggregated[key] = art
                if len(aggregated) >= count:
                    break

        if len(aggregated) >= count:
            break

    if not aggregated:
        if yfinance_rate_limited:
            raise RateLimitError(
                "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。"
            )
        if last_error is not None:
            raise RuntimeError(f"Failed to fetch market news: {last_error}") from last_error
        return []

    # 按发布时间倒序排列（None 视为最旧）
    results = list(aggregated.values())
    results.sort(key=lambda a: a.pub_date or "", reverse=True)
    return results[:count]


# ============================================================
# 以下为「抓取文章正文」相关函数。
# Yahoo 列表/搜索接口都只返回标题与链接，不返回正文。
# 想拿到全文必须再请求文章详情页并自行解析。
#
# 实现策略（双通道，依旧延续主路径 + 降级风格）：
#   1) 主路径：调用 Yahoo 内部 caas-content-web 接口，直接拿到结构化 JSON
#   2) 降级路径：直接 GET 文章 HTML 页面，用正则 + html.unescape 抽取正文
# ============================================================

# 一些第三方 publisher 不会跳到 finance.yahoo.com 域名，
# 但只要 link 是 finance.yahoo.com/m/{uuid}/... 就可以走 caas 接口。
YAHOO_CAAS_URL = (
    "https://finance.yahoo.com/caas/content/article"
    "?uuid={uuid}&serviceKey=ncp_fin"
)

# 用于从 Yahoo 文章详情页 HTML 中识别正文的正则集合。
# Yahoo 经常调整模板，这里给出多个候选，按顺序尝试匹配。
_HTML_BODY_PATTERNS = (
    # 常见模板：<div class="caas-body"> ...正文... </div>
    re.compile(r'<div[^>]+class="[^"]*caas-body[^"]*"[^>]*>(.*?)</div>\s*</article>', re.S),
    # 备用模板：<article ...> ...正文... </article>
    re.compile(r"<article\b[^>]*>(.*?)</article>", re.S),
)

# 把所有 HTML 标签替换成空格，再做空白合并
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(raw_html: str) -> str:
    """
    把 HTML 片段转成纯文本：
      1) 去掉 <script>/<style> 整段
      2) 把所有标签替换为空格
      3) html.unescape 反转义实体（&amp; → &）
      4) 多余空白合并为单空格
    Java 类比：Jsoup.parse(html).text() 的简化版本。
    """
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _http_get(url: str, timeout: int = 30) -> str:
    """
    极简 HTTP GET 工具：
    - 自动加上常用浏览器 User-Agent
    - 读取并 decode 成 utf-8 字符串
    - HTTP 429 → RateLimitError
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/json,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimitError(
                "Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。"
            ) from exc
        raise


def _extract_uuid_from_link(link: str) -> str | None:
    """
    从 Yahoo 文章链接中提取 uuid。
    形如：https://finance.yahoo.com/m/9aa99b5e-574f-393a-97eb-1de8b09c3f42/xxx.html
    """
    match = re.search(
        r"/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/",
        link,
    )
    return match.group(1) if match else None


def _fetch_article_via_caas(uuid: str) -> tuple[str | None, dict | None]:
    """
    主路径：调用 Yahoo 内部 caas 接口拿结构化 JSON。
    返回值：(正文纯文本, 接口原始 JSON)
    """
    url = YAHOO_CAAS_URL.format(uuid=uuid)
    body = _http_get(url)
    data = json.loads(body)

    # caas 接口返回结构有多种变体，逐层兜底
    items = (
        data.get("items")
        or data.get("data", {}).get("contents")
        or []
    )
    if not items:
        return None, data

    first = items[0] if isinstance(items, list) else items
    content_node = first.get("content") if isinstance(first, dict) else None
    if not content_node:
        return None, data

    # body 可能是 HTML 字符串，也可能是富文本结构
    body_html = (
        content_node.get("body")
        or content_node.get("articleBody")
        or content_node.get("articleBodyHtml")
    )
    if isinstance(body_html, dict):
        body_html = body_html.get("data") or body_html.get("html")

    if not isinstance(body_html, str) or not body_html.strip():
        return None, data

    return _strip_html(body_html), data


def _fetch_article_via_html(link: str) -> str | None:
    """
    降级路径：直接 GET 文章 HTML 页面，用正则抽取正文。
    """
    body = _http_get(link)
    if "Will be right back" in body:
        raise RuntimeError("Yahoo Finance is temporarily unavailable")

    for pattern in _HTML_BODY_PATTERNS:
        m = pattern.search(body)
        if m:
            text = _strip_html(m.group(1))
            if len(text) > 200:  # 避免抓到导航栏之类的短碎片
                return text
    return None


def fetch_article_content(link: str) -> str:
    """
    给定一篇 Yahoo Finance 新闻链接，尝试抓取并返回正文纯文本。

    参数：
        link: NewsArticle.link 字段
    返回：
        文章正文（纯文本）。无法解析时抛 RuntimeError。
    """
    if not link or not isinstance(link, str):
        raise ValueError("link is required")

    # 仅 finance.yahoo.com 自有页面才有 uuid，可以走 caas 主路径
    uuid = _extract_uuid_from_link(link) if "finance.yahoo.com" in link else None
    last_error: Exception | None = None

    if uuid:
        try:
            text, _raw = _fetch_article_via_caas(uuid)
            if text:
                return text
        except RateLimitError:
            raise
        except Exception as exc:
            last_error = exc  # caas 接口失败时回退到 HTML 抓取

    # 降级：直接拉 HTML
    try:
        text = _fetch_article_via_html(link)
        if text:
            return text
    except RateLimitError:
        raise
    except Exception as exc:
        last_error = exc

    if last_error is not None:
        raise RuntimeError(f"Failed to extract article content: {last_error}") from last_error
    raise RuntimeError("Article body not found in HTML")


def get_news_with_content(
    ticker: str | None = None,
    *,
    count: int = 10,
    tab: str = "news",
    query: str | None = None,
    market: bool = False,
    max_articles: int = 5,
) -> list[NewsArticle]:
    """
    在 get_news / get_market_news 之上再抓取每条新闻的正文。

    参数：
        ticker        : 单只股票模式必填
        count         : 列表条数
        tab           : 单股模式的 tab
        query         : market 模式下的搜索关键词
        market        : True 表示走每日财经头条，否则走单股票模式
        max_articles  : 最多抓取正文的条数（避免一次性请求过多被限流）

    返回：
        填充了 content 字段的 NewsArticle 列表。

    设计说明：
        - 抓取列表不会失败时跑得很快，但「抓正文」是 N 次额外 HTTP，
          所以用 max_articles 限制；超过的部分仍返回但不抓正文。
        - 单条正文抓取失败不影响整体结果，只是 content 留空。
    """
    if market:
        articles = get_market_news(count=count, query=query)
    else:
        if not ticker:
            raise ValueError("ticker is required when market=False")
        articles = get_news(ticker, count=count, tab=tab)

    enrich_count = max(0, min(max_articles, len(articles)))
    for art in articles[:enrich_count]:
        if not art.link:
            continue
        try:
            art.content = fetch_article_content(art.link)
        except RateLimitError:
            # 一旦被限流就停止后续抓取，避免雪上加霜
            break
        except Exception:
            # 单条抓取失败：跳过，保持 content=None
            continue

    return articles


# ============================================================
# 以下为「统一搜索接口」相关函数。
# 把【股票代码 / 关键词 / 时间区间 / 每日头条】合并成同一个入口，
# 类似 Spring Boot 里把多个 @GetMapping 整合成一个查询条件对象。
# ============================================================

def _parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """
    把用户传入的时间字符串解析成 timezone-aware 的 datetime。
    支持以下常见格式：
        - YYYY-MM-DD
        - YYYY-MM-DD HH:MM
        - YYYY-MM-DDTHH:MM:SS
        - 完整 ISO8601（带时区或 Z）
    Java 类比：DateTimeFormatter.ofPattern(...).parse(value)
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    parsed_by_date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))

    # ISO8601 中的 Z 表示 UTC，Python 的 fromisoformat 在 3.11+ 才直接支持 Z
    iso = text.replace("Z", "+00:00")

    # 尝试 ISO8601（包括带时区）
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        # 退化到几种常见格式
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                f"无法解析时间 '{value}'，请使用 ISO8601 或 YYYY-MM-DD[ HH:MM[:SS]] 格式"
            )

    # 如果用户传的是纯日期且用于 until，扩展到当天结束，符合直觉查询语义
    if end_of_day and parsed_by_date_only:
        dt = dt + timedelta(days=1) - timedelta(microseconds=1)

    # 如果用户没带时区，默认按中国时区（UTC+8）解释，再统一转 UTC 比较
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    return dt.astimezone(timezone.utc)


def _article_datetime(article: NewsArticle) -> datetime | None:
    """
    把 NewsArticle.pub_date 字符串解析成 datetime。无法解析时返回 None。
    """
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
    """
    判断文章是否命中「模糊关键词」。
    实现：把 query 拆词，所有词都需要在 标题/摘要/发布机构/正文 中出现（忽略大小写）。
    """
    haystack_parts = [
        article.title or "",
        article.summary or "",
        article.publisher or "",
        article.content or "",
    ]
    haystack = " ".join(haystack_parts).lower()
    # 简单按空格拆词；空 query 视为命中
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return True
    return all(token in haystack for token in tokens)


def _dedup_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    deduped: dict[str, NewsArticle] = {}
    for art in articles:
        key = art.id or art.link or art.title
        if not key:
            continue
        if key in deduped:
            continue
        deduped[key] = art
    return list(deduped.values())


def _gather_market_news_for_time_range(
    *,
    target_count: int,
    include_default_queries: bool = True,
) -> list[NewsArticle]:
    queries: list[str] = []
    if include_default_queries:
        queries.extend(DEFAULT_MARKET_QUERIES)
    queries.extend(MARKET_TIME_RANGE_QUERIES)

    aggregated: list[NewsArticle] = []
    per_query = min(200, max(20, target_count // max(1, len(queries)) + 8))

    for q in queries:
        try:
            batch = get_market_news(count=per_query, query=q)
        except Exception:
            continue
        aggregated.extend(batch)

    return _dedup_articles(aggregated)


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
    """
    统一新闻查询接口。所有参数均可选；组合方式如下：

    +-------------------+--------------------------------+--------------------------------+
    | ticker  | query   | 行为                                                            |
    +---------+---------+-----------------------------------------------------------------+
    | 有      | 空      | 按股票代码抓新闻（沿用 get_news）                                |
    | 有      | 有      | 按股票代码抓新闻 → 用 query 在标题/摘要/正文中模糊过滤            |
    | 空      | 有      | 按关键词搜索新闻（沿用 get_market_news 的 Search 通道）           |
    | 空      | 空      | 抓每日财经头条                                                    |
    +---------+---------+-----------------------------------------------------------------+

    时间过滤：
        - since 与 until 任一存在时，按 pub_date 区间过滤（含端点）
        - 都不传时，返回「最新」结果（即 Yahoo 接口默认顺序）

    其它参数：
        count        : 期望返回条数（1-200）
        tab          : 仅 ticker 模式生效（news/all/press releases）
        with_content : 是否抓取正文（默认抓前 max_articles 条）
        max_articles : with_content 启用时最多抓取正文的条数

    Java 类比：
        相当于把多个 Repository 方法合并为一个 NewsSearchService.search(criteria) 方法，
        criteria 内置 ticker/query/since/until/count 等条件。
    """
    # ------------- 1. 参数校验 -------------
    if count < 1 or count > 200:
        raise ValueError("count must be between 1 and 200")

    since_dt = _parse_datetime(since)
    until_dt = _parse_datetime(until, end_of_day=True)
    if since_dt and until_dt and since_dt > until_dt:
        raise ValueError("since 不能晚于 until")

    # ------------- 2. 选择数据源 -------------
    # 为了在时间过滤后还能凑够 count 条，先多抓一些（最多 200）
    fetch_count = min(200, max(count * 3, count + 20)) if (since_dt or until_dt) else count

    if ticker:
        # 走单股票通道
        articles = get_news(ticker, count=fetch_count, tab=tab)
    elif query:
        # 走关键词通道（直接复用 get_market_news 的实现，传入 query）
        articles = get_market_news(count=fetch_count, query=query)
    else:
        # 啥都没传：
        # 1) 无时间条件：抓每日头条
        # 2) 有时间条件：扩展关键词池多轮抓取，提高时间区间命中率
        if since_dt or until_dt:
            articles = _gather_market_news_for_time_range(target_count=max(fetch_count, count * 6))
        else:
            articles = get_market_news(count=fetch_count)

    # ------------- 3. 关键词模糊过滤 -------------
    # ticker + query 组合时，需要在 ticker 结果上再用 query 过滤；
    # 单独 query 时其实 Yahoo Search 已经做过相关性匹配，但保险起见再做一次本地过滤。
    if query:
        articles = [a for a in articles if _matches_query(a, query)]

    # ------------- 4. 时间区间过滤 -------------
    if since_dt or until_dt:
        filtered: list[NewsArticle] = []
        for art in articles:
            dt = _article_datetime(art)
            if dt is None:
                # 无法解析时间的条目：默认排除（避免污染时间范围结果）
                continue
            if since_dt and dt < since_dt:
                continue
            if until_dt and dt > until_dt:
                continue
            filtered.append(art)
        articles = filtered

    # ------------- 5. 排序 + 截断 -------------
    articles.sort(key=lambda a: a.pub_date or "", reverse=True)
    articles = articles[:count]

    # ------------- 6. 可选：抓正文 -------------
    if with_content and articles:
        enrich_count = max(0, min(max_articles, len(articles)))
        for art in articles[:enrich_count]:
            if not art.link:
                continue
            try:
                art.content = fetch_article_content(art.link)
            except RateLimitError:
                break
            except Exception:
                continue

    return articles
