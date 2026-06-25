import html
import json
import re
import urllib.error
import urllib.request

from app.services.common import RateLimitError

YAHOO_CAAS_URL = "https://finance.yahoo.com/caas/content/article?uuid={uuid}&serviceKey=ncp_fin"
_HTML_BODY_PATTERNS = (
    re.compile(r'<div[^>]+class="[^"]*caas-body[^"]*"[^>]*>(.*?)</div>\s*</article>', re.S),
    re.compile(r"<article\b[^>]*>(.*?)</article>", re.S),
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(raw_html: str) -> str:
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _http_get(url: str, timeout: int = 30) -> str:
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
            raise RateLimitError("Yahoo Finance 请求过于频繁，已被限流。请等待 5-10 分钟后再试。") from exc
        raise


def _extract_uuid_from_link(link: str) -> str | None:
    match = re.search(
        r"/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/",
        link,
    )
    return match.group(1) if match else None


def _fetch_article_via_caas(uuid: str) -> tuple[str | None, dict | None]:
    url = YAHOO_CAAS_URL.format(uuid=uuid)
    body = _http_get(url)
    data = json.loads(body)

    items = data.get("items") or data.get("data", {}).get("contents") or []
    if not items:
        return None, data

    first = items[0] if isinstance(items, list) else items
    content_node = first.get("content") if isinstance(first, dict) else None
    if not content_node:
        return None, data

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
    body = _http_get(link)
    if "Will be right back" in body:
        raise RuntimeError("Yahoo Finance is temporarily unavailable")

    for pattern in _HTML_BODY_PATTERNS:
        matched = pattern.search(body)
        if matched:
            text = _strip_html(matched.group(1))
            if len(text) > 200:
                return text
    return None


def fetch_article_content(link: str) -> str:
    if not link or not isinstance(link, str):
        raise ValueError("link is required")

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
            last_error = exc

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
