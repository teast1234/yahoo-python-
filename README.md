# Yahoo Finance News (Python + yfinance)

使用 Python 和 [yfinance](https://github.com/ranaroussi/yfinance) 获取 Yahoo 财经新闻，支持命令行和 HTTP API。
新闻来源于yfinance和yahoo的公共接口，只能获取到当天的新闻数据，并且只支持股票和关键词搜索

## 环境要求

- Python 3.10+

## 安装

```bash
pip install -r requirements.txt
```

## 命令行用法

> 推荐：CLI 已经统一为「一条命令满足所有查询」。
> 没传任何条件时自动拉取最新财经头条，可任意组合 `ticker / --query / --since / --until`。

```bash
# 1) 不传任何条件 → 自动拉取最新财经头条
python -m app.main --count 20

# 2) 按股票代码搜索
python -m app.main AAPL --count 5
python -m app.main MSFT --count 20 --tab all

# 3) 按关键词模糊搜索（不限定股票）
python -m app.main --query "AI chip" --count 10

# 4) 股票代码 + 关键词（先按股票拉，再用关键词过滤）
python -m app.main AAPL --query iphone --count 10

# 5) 时间区间过滤（任一可省略）
python -m app.main AAPL --since 2026-06-01 --until 2026-06-23
python -m app.main --query "Federal Reserve" --since "2026-06-01 09:00"

# 6) 顺带抓正文（速度较慢，默认抓前 5 条，可用 --max-articles 调整）
python -m app.main AAPL --with-content --max-articles 3

# 7) 抓一篇指定文章正文
python -m app.main --article "https://finance.yahoo.com/m/<uuid>/xxx.html"

# 8) 启动 HTTP 服务（默认端口已改为 8090）
python -m app.main --serve
# 或显式指定
python -m app.main --serve --port 8090
```

## HTTP API

先启动服务（会占用当前终端，按 `Ctrl+C` 停止）：

```bash
python -m app.main --serve --port 8090
```

然后在浏览器或另一个终端访问：

```text
http://localhost:8090/                              # 首页说明
http://localhost:8090/docs                          # 交互式 API 文档

# ★ 推荐：统一搜索接口（ticker / query / 时间区间 任意组合）
http://localhost:8090/api/search?count=20                                  # 不传条件 → 最新财经头条
http://localhost:8090/api/search?ticker=AAPL&count=5                       # 按股票代码
http://localhost:8090/api/search?query=AI&count=10                         # 按关键词模糊搜索
http://localhost:8090/api/search?ticker=AAPL&query=iphone                  # 股票 + 关键词
http://localhost:8090/api/search?since=2026-06-01&until=2026-06-23         # 按时间区间
http://localhost:8090/api/search?ticker=AAPL&since=2026-06-01              # 股票 + 起始时间
http://localhost:8090/api/search?query=AI&with_content=true&max_articles=3 # 顺带抓正文

# 兼容老接口
http://localhost:8090/api/news/AAPL?count=5&tab=news
http://localhost:8090/api/news?count=20
http://localhost:8090/api/article?link=<encoded url>
```

命令行查询需在**另一个终端**运行，不能与 `--serve` 同时占用同一窗口：

```bash
python -m app.main AAPL --count 5
python -m app.main MSFT --count 20 --tab all
```

### `/api/search` 参数（统一查询接口，推荐使用）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `ticker` | 股票代码（可选） | 空 |
| `query` | 关键词（模糊匹配标题/摘要/发布机构/正文，多词以空格分隔，需全部命中） | 空 |
| `since` | 起始时间，支持 `YYYY-MM-DD` / `YYYY-MM-DD HH:MM` / ISO8601（无时区时按中国时间 UTC+8） | 空 |
| `until` | 截止时间，格式同 `since`（无时区时按中国时间 UTC+8） | 空 |
| `count` | 新闻条数 | `20`（1-200） |
| `tab` | 仅 `ticker` 模式生效（`news` / `all` / `press releases`） | `news` |
| `with_content` | 是否抓取每条新闻的正文 | `false` |
| `max_articles` | 启用 `with_content` 时最多抓取正文的条数 | `5`（1-20） |

参数组合行为：

| `ticker` | `query` | 行为 |
|---------|---------|------|
| 有 | 空 | 按股票代码抓新闻 |
| 有 | 有 | 按股票代码抓 → 用关键词在标题/摘要/正文中过滤 |
| 空 | 有 | 按关键词搜索全站新闻 |
| 空 | 空 | 抓每日财经头条 |

`since` / `until` 任意组合：单传则形成开区间；都不传则返回最新新闻。

说明：当仅传时间、不传 `query` 时，系统会自动使用两组可配置关键词池（默认池 + 时间增强池）进行聚合抓取，再执行时间过滤，以尽量返回满足 `count` 的结果。

### `/api/news/{ticker}` 参数（旧接口）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `ticker` | 股票代码 | 必填 |
| `count` | 新闻条数 | `10`（1-200） |
| `tab` | 新闻类型 | `news`（可选：`all`、`press releases`） |
| `with_content` | 是否抓取每条新闻的正文 | `false` |
| `max_articles` | 启用 `with_content` 时最多抓取正文的条数 | `5`（1-20） |

### `/api/news` 参数（旧接口，每日财经头条）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `count` | 新闻条数 | `20`（1-200） |
| `query` | 搜索关键词；留空则聚合 `stock market` / `finance` / `economy` 大盘头条 | 空 |
| `with_content` | 是否抓取每条新闻的正文（会显著变慢） | `false` |
| `max_articles` | 启用 `with_content` 时最多抓取正文的条数 | `5`（1-20） |

### `/api/article` 参数（能够将获取到的url链接转化为正文string）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `link` | Yahoo Finance 文章 URL | 必填 |

返回字段：

```json
{
  "link": "https://finance.yahoo.com/m/<uuid>/xxx.html",
  "content": "文章正文纯文本……",
  "length": 12345
}
```

## 项目结构

```text
newstock/
├── app/
│   ├── presentation/            # 接入层（HTTP/CLI）
│   │   ├── http_api.py          # FastAPI 路由
│   │   └── cli_app.py           # 命令行入口逻辑
│   ├── services/                # 业务层（按职责拆分）
│   │   ├── news_fetcher.py      # 新闻抓取（ticker/market）
│   │   ├── article_content.py   # 文章正文抓取与解析
│   │   ├── news_enricher.py     # 正文富化
│   │   ├── news_search.py       # 统一搜索/过滤/排序
│   │   └── common.py            # 公共常量与工具
│   ├── news_service.py          # 兼容导出层
│   ├── main.py                  # 薄入口（导出 app/run_cli）
│   └── models.py                # 数据模型
├── requirements.txt
└── README.md
```
