"""Core build engine orchestrating file discovery and rendering."""

from pathlib import Path
from typing import List, Tuple

from minisite.config import SiteConfig
from minisite.exceptions import RenderError, SourceNotFoundError
from minisite.models import BaseItem, Page, Post


class BuildEngine:
    """Manages document discovery, processing, and output generation."""

    def __init__(self, config: SiteConfig, base_dir: Path) -> None:
        self.config = config
        self.base_dir = Path(base_dir)
        self.source_dir = self.base_dir / config.source_dir
        self.output_dir = self.base_dir / config.output_dir

    def discover_documents(self) -> List[BaseItem]:
        """Discover and instantiate all document items in source directory.

        Raises:
            SourceNotFoundError: If source directory does not exist.
        """
        if not self.source_dir.is_dir():
            raise SourceNotFoundError(str(self.source_dir))

        items: List[BaseItem] = []
        for file_path in sorted(self.source_dir.rglob("*.txt")):
            slug = file_path.stem
            raw_text = file_path.read_text(encoding="utf-8")
            title, metadata, content = self._parse_file(raw_text, default_title=slug)

            doc_type = metadata.get("type", "post").lower()
            if doc_type == "page":
                item: BaseItem = Page(
                    slug=slug,
                    title=title,
                    content_raw=content,
                    source_path=file_path,
                    metadata=metadata,
                )
            else:
                item = Post(
                    slug=slug,
                    title=title,
                    content_raw=content,
                    source_path=file_path,
                    metadata=metadata,
                    tags=[t.strip() for t in metadata.get("tags", "").split(",") if t.strip()],
                )

            # Filter drafts if not explicitly included in config
            if item.is_published or self.config.include_drafts:
                items.append(item)

        return items

    def _parse_file(self, raw_text: str, default_title: str) -> Tuple[str, dict[str, str], str]:
        """Simple line-based metadata parser.

        Header lines starting with '@key: value' are parsed as metadata.
        """
        lines = raw_text.splitlines()
        metadata: dict[str, str] = {}
        content_lines: List[str] = []

        for line in lines:
            if line.startswith("@") and ":" in line:
                key, val = line[1:].split(":", 1)
                metadata[key.strip()] = val.strip()
            else:
                content_lines.append(line)

        title = metadata.get("title", default_title)
        content = "\n".join(content_lines).strip()
        return title, metadata, content

    def build(self) -> int:
        """Run the full build pipeline.

        Returns:
            Number of successfully rendered and written documents.

        Raises:
            RenderError: When writing output file fails.
        """
        documents = self.discover_documents()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        built_count = 0
        for doc in documents:
            try:
                html = doc.render_html()
                out_path = self.output_dir / f"{doc.slug}.html"
                out_path.write_text(html, encoding="utf-8")
                built_count += 1
            except Exception as exc:
                raise RenderError(doc.slug, str(exc)) from exc

        return built_count
