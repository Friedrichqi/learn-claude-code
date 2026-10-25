"""GAIA's web tools for the s15 benchmark profile: `web_search` and `fetch_url`.

They run on the host side (in the s15 child), not in the task container, and use the search API whose key the
user put in secrets.env (SERPER_API_KEY, TAVILY_API_KEY or BRAVE_API_KEY; the first one present wins unless
S15_SEARCH_PROVIDER names one). Pages that mirror benchmark data are filtered out, as HAL's GAIA agent does,
so the answer cannot be looked up from a copy of the dataset.
"""
from __future__ import annotations

import html
import io
import os
import re
from html.parser import HTMLParser

import httpx

BLOCKED = (
    "gaia-benchmark", "huggingface.co/datasets", "hf.co/datasets", "huggingface.co/spaces/gaia",
    "gaia_benchmark", "/gaia-benchmark", "paperswithcode.com/dataset/gaia", "kaggle.com/datasets",
    "hal.cs.princeton.edu", "github.com/princeton-pli/hal-harness",
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

TOOLS = [
    {"name": "web_search",
     "description": "Search the web. Returns titles, URLs and snippets of the top results.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "num_results": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "fetch_url",
     "description": "Fetch a web page or PDF and return its text (truncated to max_chars, default 20000; "
                    "use offset to read further).",
     "input_schema": {"type": "object",
                      "properties": {"url": {"type": "string"},
                                     "max_chars": {"type": "integer"},
                                     "offset": {"type": "integer"}},
                      "required": ["url"]}},
]

STATS = {"searches": 0, "fetches": 0, "blocked": 0, "errors": 0}


def blocked(url: str) -> bool:
    u = (url or "").lower()
    return any(b in u for b in BLOCKED)


def _provider() -> tuple[str, str]:
    order = ["serper", "tavily", "brave"]
    want = os.environ.get("S15_SEARCH_PROVIDER", "").lower()
    if want:
        order = [want]
    for name in order:
        key = os.environ.get(f"{name.upper()}_API_KEY")
        if key:
            return name, key
    return "", ""


def search(query: str, num: int = 8) -> list[dict]:
    name, key = _provider()
    num = max(1, min(int(num or 8), 10))
    with httpx.Client(timeout=30) as http:
        if name == "serper":
            r = http.post("https://google.serper.dev/search", headers={"X-API-KEY": key},
                          json={"q": query, "num": num})
            r.raise_for_status()
            items = [{"title": i.get("title"), "url": i.get("link"), "snippet": i.get("snippet")}
                     for i in r.json().get("organic", [])]
        elif name == "tavily":
            r = http.post("https://api.tavily.com/search",
                          json={"api_key": key, "query": query, "max_results": num})
            r.raise_for_status()
            items = [{"title": i.get("title"), "url": i.get("url"), "snippet": i.get("content")}
                     for i in r.json().get("results", [])]
        elif name == "brave":
            r = http.get("https://api.search.brave.com/res/v1/web/search",
                         headers={"X-Subscription-Token": key, "Accept": "application/json"},
                         params={"q": query, "count": num})
            r.raise_for_status()
            items = [{"title": i.get("title"), "url": i.get("url"), "snippet": i.get("description")}
                     for i in r.json().get("web", {}).get("results", [])]
        else:
            raise RuntimeError("no search API key configured (SERPER_API_KEY, TAVILY_API_KEY or BRAVE_API_KEY)")
    return items


def run_web_search(query: str, num_results: int = 8) -> str:
    STATS["searches"] += 1
    try:
        items = search(query, num_results)
    except Exception as exc:                                    # noqa: BLE001
        STATS["errors"] += 1
        return f"Error: search failed: {type(exc).__name__}: {exc}"
    kept = []
    for item in items:
        if blocked(item.get("url") or ""):
            STATS["blocked"] += 1
            continue
        kept.append(item)
    if not kept:
        return "(no results)"
    lines = []
    for n, item in enumerate(kept, 1):
        snippet = re.sub(r"\s+", " ", html.unescape(item.get("snippet") or "")).strip()
        lines.append(f"{n}. {item.get('title') or '(untitled)'}\n   {item.get('url')}\n   {snippet}")
    return "\n".join(lines)


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(text: str) -> str:
    parser = _Text()
    parser.feed(text)
    out = "".join(parser.parts)
    out = re.sub(r"[ \t\r\f\v]+", " ", out)
    out = re.sub(r"\n\s*\n\s*\n+", "\n\n", out)
    return out.strip()


def run_fetch_url(url: str, max_chars: int = 20000, offset: int = 0) -> str:
    STATS["fetches"] += 1
    if blocked(url):
        STATS["blocked"] += 1
        return "Error: this URL is not available in this environment."
    try:
        with httpx.Client(timeout=45, follow_redirects=True, headers={"User-Agent": UA}) as http:
            r = http.get(url)
        if blocked(str(r.url)):
            STATS["blocked"] += 1
            return "Error: this URL is not available in this environment."
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        if "pdf" in ctype or str(r.url).lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(r.content))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        elif "html" in ctype or "<html" in r.text[:2000].lower():
            text = html_to_text(r.text)
        else:
            text = r.text
    except Exception as exc:                                    # noqa: BLE001
        STATS["errors"] += 1
        return f"Error: fetch failed: {type(exc).__name__}: {exc}"
    offset = max(int(offset or 0), 0)
    max_chars = max(1000, min(int(max_chars or 20000), 60000))
    chunk = text[offset:offset + max_chars]
    rest = len(text) - offset - len(chunk)
    tail = f"\n... ({rest} more characters; call again with offset={offset + len(chunk)})" if rest > 0 else ""
    return (chunk or "(empty page)") + tail
