"""Exception hierarchy for minisite."""

from typing import Optional


class MiniSiteError(Exception):
    """Base exception for all minisite operations."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or "ERR_INTERNAL"


class ConfigError(MiniSiteError):
    """Raised when configuration file is missing, invalid or fails validation."""

    def __init__(self, message: str, field_name: Optional[str] = None) -> None:
        super().__init__(message, code="ERR_CONFIG")
        self.field_name = field_name


class SourceNotFoundError(MiniSiteError):
    """Raised when source content directory does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Source content path not found: {path}", code="ERR_SOURCE_NOT_FOUND")
        self.path = path


class RenderError(MiniSiteError):
    """Raised when markdown or template rendering encounters a failure."""

    def __init__(self, item_name: str, reason: str) -> None:
        super().__init__(f"Failed to render '{item_name}': {reason}", code="ERR_RENDER")
        self.item_name = item_name
        self.reason = reason
