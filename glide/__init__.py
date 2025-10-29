"""Top-level package for Glide data tooling."""

from importlib import metadata as _metadata

try:  # pragma: no cover - best effort metadata lookup
    __version__ = _metadata.version("glide")
except _metadata.PackageNotFoundError:  # pragma: no cover - local execution fallback
    __version__ = "0.0.0"

__all__ = ["__version__"]
