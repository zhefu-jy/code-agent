"""Data models representing site documents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class BaseItem(ABC):
    """Abstract base document."""

    slug: str
    title: str
    content_raw: str
    source_path: Path
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def is_published(self) -> bool:
        """Check whether document is published."""
        status = self.metadata.get("status", "published").lower()
        return status != "draft"

    @abstractmethod
    def render_html(self) -> str:
        """Render raw content into HTML format."""
        raise NotImplementedError


@dataclass
class Page(BaseItem):
    """Standard static page (e.g. About, Contact)."""

    show_in_nav: bool = True

    def render_html(self) -> str:
        """Render standalone page."""
        return (
            f"<article class=\"page\">\n"
            f"  <h1>{self.title}</h1>\n"
            f"  <div class=\"content\">{self.content_raw}</div>\n"
            f"</article>"
        )


@dataclass
class Post(BaseItem):
    """Blog post item with date and tag classification."""

    created_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.created_at is None and "date" in self.metadata:
            try:
                self.created_at = datetime.fromisoformat(self.metadata["date"])
            except ValueError:
                self.created_at = None

    def render_html(self) -> str:
        """Render post with metadata header."""
        date_str = self.created_at.strftime("%Y-%m-%d") if self.created_at else "Unknown date"
        tag_html = " ".join(f"<span class=\"tag\">#{t}</span>" for t in self.tags)
        return (
            f"<article class=\"post\">\n"
            f"  <header>\n"
            f"    <h1>{self.title}</h1>\n"
            f"    <time>{date_str}</time>\n"
            f"    <div class=\"tags\">{tag_html}</div>\n"
            f"  </header>\n"
            f"  <div class=\"body\">{self.content_raw}</div>\n"
            f"</article>"
        )
