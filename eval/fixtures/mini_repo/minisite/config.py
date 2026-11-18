"""Configuration loading and validation for minisite."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from minisite.exceptions import ConfigError

DEFAULT_CONFIG_FILENAME = "minisite.json"
SUPPORTED_THEMES = ["minimal", "classic", "dark"]


@dataclass
class SiteConfig:
    """Holds parsed site configuration."""

    title: str = "My Mini Site"
    author: str = "Anonymous"
    theme: str = "minimal"
    source_dir: str = "content"
    output_dir: str = "dist"
    include_drafts: bool = False
    max_posts: int = 100
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "title": self.title,
            "author": self.author,
            "theme": self.theme,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "include_drafts": self.include_drafts,
            "max_posts": self.max_posts,
            "tags": list(self.tags),
        }


def validate_config(config: SiteConfig) -> None:
    """Validate SiteConfig semantics.

    Raises:
        ConfigError: If any configuration value is invalid.
    """
    if not config.title or not config.title.strip():
        raise ConfigError("Site title cannot be empty", field_name="title")

    if config.theme not in SUPPORTED_THEMES:
        raise ConfigError(
            f"Unsupported theme '{config.theme}'. Supported: {SUPPORTED_THEMES}",
            field_name="theme",
        )

    if config.max_posts <= 0:
        raise ConfigError("max_posts must be positive integer", field_name="max_posts")


def load_config(config_path: Optional[Path] = None) -> SiteConfig:
    """Load and validate configuration from file or defaults.

    Args:
        config_path: Optional path to JSON configuration file.

    Returns:
        Validated SiteConfig instance.

    Raises:
        ConfigError: If JSON syntax is invalid or configuration fails validation.
    """
    if config_path is None:
        return SiteConfig()

    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}", field_name="path")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ConfigError(f"Invalid JSON configuration in {path}: {err}", field_name="json") from err

    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be a JSON object", field_name="root")

    cfg = SiteConfig(
        title=str(data.get("title", "My Mini Site")),
        author=str(data.get("author", "Anonymous")),
        theme=str(data.get("theme", "minimal")),
        source_dir=str(data.get("source_dir", "content")),
        output_dir=str(data.get("output_dir", "dist")),
        include_drafts=bool(data.get("include_drafts", False)),
        max_posts=int(data.get("max_posts", 100)),
        tags=list(data.get("tags", [])),
    )
    validate_config(cfg)
    return cfg
