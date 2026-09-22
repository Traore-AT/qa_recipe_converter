import os
import fnmatch
from pathlib import Path
from typing import Iterator


SEARCH_EXTENSIONS = {'.docx', '.doc', '.xlsx'}

# Directories to skip during search
SKIP_DIRS = {
    '__pycache__', '.git', 'node_modules', '.venv', 'venv',
    'env', '.env', 'dist', 'build', '.idea', '.vscode',
    'Library', 'System', 'Windows', 'Program Files', 'Program Files (x86)',
}


def search_files(
    query: str,
    search_root: str | None = None,
    extensions: list[str] | None = None,
    max_results: int = 20,
) -> list[dict]:
    """
    Search for files matching `query` on the local filesystem.

    Args:
        query: Filename fragment to search for (case-insensitive)
        search_root: Directory to search from (defaults to user home)
        extensions: List of extensions to filter (e.g. ['.docx', '.xlsx'])
        max_results: Maximum number of results to return

    Returns:
        List of dicts with 'name', 'path', 'size', 'extension'
    """
    if not query or len(query) < 2:
        return []

    if extensions is None:
        extensions = list(SEARCH_EXTENSIONS)
    extensions = {ext.lower() for ext in extensions}

    if search_root is None:
        search_root = str(Path.home())

    query_lower = query.lower()
    results = []

    try:
        for root, dirs, files in os.walk(search_root, topdown=True, followlinks=False):
            # Skip unwanted directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]

            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in extensions:
                    continue
                if query_lower not in filename.lower():
                    continue

                full_path = os.path.join(root, filename)
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    continue

                results.append({
                    'name': filename,
                    'path': full_path,
                    'size': size,
                    'size_human': _human_size(size),
                    'extension': ext,
                })

                if len(results) >= max_results:
                    return results

    except PermissionError:
        pass

    # Sort: exact matches first, then by name
    results.sort(key=lambda r: (
        0 if r['name'].lower().startswith(query_lower) else 1,
        r['name'].lower()
    ))

    return results


def _human_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"
