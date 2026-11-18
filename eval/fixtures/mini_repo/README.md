# minisite

A minimal, standard-library static site builder designed for testing code analysis and retrieval tools.

## Architecture
- `minisite.exceptions`: Hierarchy of custom exceptions (`MiniSiteError`, `ConfigError`, `RenderError`, etc.).
- `minisite.config`: Configuration loading (`SiteConfig`), defaults, and validation.
- `minisite.models`: Document abstractions (`BaseItem`, `Page`, `Post`) with metadata and rendering.
- `minisite.engine`: Main orchestration engine (`BuildEngine`) reading source files and generating HTML.
- `minisite.cli`: Command line entrypoint with argument parsing and execution control.
