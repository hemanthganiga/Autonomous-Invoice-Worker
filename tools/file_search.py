"""Find local PDF files while skipping project-generated directories."""

import os
from pathlib import Path

DEFAULT_IGNORED_DIRS = frozenset(
    {".git", ".venv", ".pip-cache", ".playwright-browsers", "__pycache__"}
)


def find_pdf_files(
    root: str | Path,
    *,
    recursive: bool = True,
    ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
) -> list[Path]:
    """Return sorted PDF paths beneath ``root``; extension matching is case-insensitive."""
    root_path = Path(root).expanduser()
    if not root_path.exists():
        raise FileNotFoundError(f"Search root does not exist: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"Search root is not a directory: {root_path}")

    ignored_names = {name.casefold() for name in ignored_dirs}
    matches: list[Path] = []

    if recursive:
        for current, directories, filenames in os.walk(root_path):
            directories[:] = sorted(
                name for name in directories if name.casefold() not in ignored_names
            )
            matches.extend(
                Path(current) / filename
                for filename in filenames
                if Path(filename).suffix.casefold() == ".pdf"
            )
    else:
        matches = [
            path
            for path in root_path.iterdir()
            if path.is_file() and path.suffix.casefold() == ".pdf"
        ]

    return sorted(matches, key=lambda path: str(path).casefold())