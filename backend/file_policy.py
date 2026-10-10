"""Small, conservative policies for review file presentation."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


_RESOURCE_SUFFIXES = frozenset(
    {
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".avif",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        # Fonts
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        # Audio
        ".mp3",
        ".wav",
        ".ogg",
        ".flac",
        ".aac",
        ".m4a",
        # Video
        ".mp4",
        ".mov",
        ".webm",
        ".avi",
        ".mkv",
        # 3D and ordinary design resources
        ".glb",
        ".gltf",
        ".fbx",
        ".psd",
        ".sketch",
    }
)

_HIDDEN_STATUSES = frozenset({"A", "M", "C"})

_DOCUMENT_SUFFIXES = frozenset({
    '.md', '.markdown', '.mdown', '.mkd', '.rst', '.adoc', '.asciidoc',
    '.pdf', '.doc', '.docx', '.odt', '.rtf', '.ppt', '.pptx', '.odp',
})
_DOCUMENT_NAMES = frozenset({'readme', 'changelog', 'changes', 'license', 'licence',
                             'copying', 'notice', 'authors', 'contributing'})


def is_documentation_path(path):
    if not isinstance(path, str) or not path or '\x00' in path:
        return False
    item = PurePosixPath(path)
    return (item.suffix.lower() in _DOCUMENT_SUFFIXES
            or item.name.lower() in _DOCUMENT_NAMES
            or (item.suffix.lower() == '.txt' and item.stem.lower() in _DOCUMENT_NAMES))


def is_hidden_document(file):
    if not isinstance(file, dict) or not is_documentation_path(file.get('path')):
        return False
    # A code/document rename still changes executable code; keep that record.
    if str(file.get('status', '')).upper().startswith('R'):
        return is_documentation_path(file.get('old_path'))
    return True


def is_hidden_resource(file: dict[str, Any]) -> bool:
    """Return whether a changed file is excluded from code review display.

    Classification is deliberately based on the status and final suffix only.
    Directory names do not make source, configuration, or data files hidden.
    Static-resource renames, deletions, and type changes remain visible.
    Documentation is excluded for every status, except code/document renames.
    """

    if not isinstance(file, dict):
        return False
    if is_hidden_document(file):
        return True
    status = file.get("status")
    if not isinstance(status, str) or not status:
        return False
    if status[:1].upper() not in _HIDDEN_STATUSES:
        return False
    path = file.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    try:
        suffix = PurePosixPath(path).suffix.lower()
    except (TypeError, ValueError):
        return False
    return suffix in _RESOURCE_SUFFIXES


__all__ = ["is_hidden_resource", "is_hidden_document", "is_documentation_path"]
