from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


def write_vector(path: Path, values: Sequence[int | float]) -> None:
    """
    Write a numeric vector to disk using the standard vector contract format.

    Format:
    - first line: vector length,
    - one value per line thereafter.

    Args:
        path: Destination file path.
        values: Sequence of numeric values to serialize.
    """
    # Ensure parent directory exists before writing.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"{len(values)}\n")
        for v in values:
            f.write(f"{v}\n")


def write_edges(path: Path, edges: Iterable[Tuple[int, int, float]]) -> int:
    """
    Write weighted edges to disk using the standard edge contract format.

    Format:
    - first line: number of edges,
    - each subsequent line: "i j w".

    Args:
        path: Destination file path.
        edges: Iterable of (i, j, weight) tuples.

    Returns:
        The number of written edges.
    """
    # Materialize iterable once to compute length and ensure deterministic output.
    edges_list: List[Tuple[int, int, float]] = list(edges)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"{len(edges_list)}\n")
        for i, j, w in edges_list:
            f.write(f"{i} {j} {w}\n")
    return len(edges_list)


def safe_write(path: Path, overwrite: bool, write_fn, *args, **kwargs) -> bool:
    """
    Safely write a file with optional overwrite protection.

    Behavior:
    - skip write if file exists and overwrite=False,
    - otherwise delegate to write_fn.

    Args:
        path: Target file path.
        overwrite: Whether to overwrite existing files.
        write_fn: Callable responsible for actual file writing.
        *args, **kwargs: Forwarded to write_fn.

    Returns:
        True if file was written, False if skipped.
    """
    # Skip write when file exists and overwrite is disabled.
    if path.exists() and not overwrite:
        return False
    write_fn(path, *args, **kwargs)
    return True