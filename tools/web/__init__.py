"""
ISHTI Web Capabilities Registry
Exposes unified web search, web fetch, content extraction, and multi-provider interfaces.
"""

from .provider import WebSearchProvider, WebSearchResult
from .search import MultiProviderSearch, get_search_manager, web_search
from .fetch import WebFetcher
from .extract import ContentExtractor

__all__ = [
    "WebSearchProvider",
    "WebSearchResult",
    "MultiProviderSearch",
    "get_search_manager",
    "web_search",
    "WebFetcher",
    "ContentExtractor",
]
