"""Web tools: web_search and web_fetch."""

import os
import re
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from tavily import TavilyClient

from loguru import logger


# Constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5


def web_search(api_key: str, query: str, max_results: int = 5) -> str:
    if not api_key:
        return "Error: Tavily API key not configured"

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )

        if not response.get("results"):
            return f"No results for: {query}"

        lines = [f"Results for: {query}\n"]
        for i, result in enumerate(response["results"][:max_results], 1):
            title = result.get("title", "")
            url = result.get("url", "")
            content = result.get("content", "")

            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            if content:
                # Truncate content if too long
                content = content[:300] + "..." if len(content) > 300 else content
                lines.append(f"   {content}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("Web search failed: {}", e)
        return f"Search error: {str(e)}"


def web_fetch(url: str, timeout: int = 10) -> str:
    try:
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"

        headers = {"User-Agent": USER_AGENT}

        with httpx.Client(headers=headers, follow_redirects=True, max_redirects=MAX_REDIRECTS, timeout=timeout) as client:
            response = client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")

            if "html" in content_type.lower():
                return _extract_html_text(response.text)
            else:
                return response.text[:10000]  # Limit to 10k chars

    except httpx.HTTPError as e:
        return f"HTTP error: {str(e)}"
    except Exception as e:
        logger.error("Web fetch failed: {}", e)
        return f"Fetch error: {str(e)}"


def _extract_html_text(html: str) -> str:
    """Extract readable text from HTML."""
    try:
        from readability import Document

        doc = Document(html)
        title = doc.title()
        content = doc.summary()

        # Clean up HTML tags
        import html as html_module

        text = re.sub(r"<[^>]+>", " ", content)
        text = html_module.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()

        if title:
            return f"{title}\n\n{text}"
        return text

    except ImportError:
        # Fallback if readability not available
        import html as html_module

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.I | re.S)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_module.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]


# Pydantic schemas for tool inputs
class WebSearchInput(BaseModel):
    """Input schema for web_search tool."""

    query: str = Field(description="Search query")
    count: int = Field(default=5, description="Number of results (1-10)", ge=1, le=10)


class WebFetchInput(BaseModel):
    """Input schema for web_fetch tool."""

    url: str = Field(description="URL to fetch")
    timeout: int = Field(default=10, description="Request timeout in seconds", ge=1, le=60)


def create_web_search_tool(api_key: str, max_results: int = 5) -> StructuredTool:
    """创建一个web搜索工具."""

    def _search(query: str, count: int = 5) -> str:
        return web_search(api_key, query, min(count, max_results))

    return StructuredTool.from_function(
        name="web_search",
        func=_search,
        description="Search the web. Returns titles, URLs, and snippets. Use this to find current information.",
        args_schema=WebSearchInput,
    )


def create_web_fetch_tool() -> StructuredTool:
    """创建一个web抓取工具."""

    return StructuredTool.from_function(
        name="web_fetch",
        func=web_fetch,
        description="Fetch and extract text from a URL. Use this to read web pages.",
        args_schema=WebFetchInput,
    )
