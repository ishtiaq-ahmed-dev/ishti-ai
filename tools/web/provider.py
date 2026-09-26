"""
ISHTI Web Search Provider Abstraction
Defines the unified data contracts and abstract interfaces for web search,
fetching, and content extraction.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import urllib.parse


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str
    source: str = ""
    publication_date: str = ""
    content: str = ""
    verified: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.source and self.url:
            try:
                parsed = urllib.parse.urlparse(self.url)
                self.source = parsed.netloc.replace("www.", "")
            except Exception:
                self.source = "Web"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "publication_date": self.publication_date,
            "content": self.content[:300] if self.content else "",
            "verified": self.verified,
        }


class WebSearchProvider(ABC):
    """Abstract interface for all ISHTI web search providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""
        pass

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[WebSearchResult]:
        """Executes a search query and returns structured results."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Health-check probe to determine provider readiness."""
        pass
