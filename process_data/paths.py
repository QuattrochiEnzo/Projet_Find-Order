from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


# Map user-friendly aliases to canonical instance directories.
ALIASES: Dict[str, str] = {
    "toy": "warehouse_toy",
    "market": "warehouse_big_/warehouse_big_market",
    "family": "warehouse_big_/warehouse_big_family",
    "category": "warehouse_big_/warehouse_big_category",
}


@dataclass(frozen=True)
class ProjectPaths:
    """Centralize project-level directories.

    Attributes:
        root: Project root directory (parent of tools/ and data/).
        data_dir: Directory containing all instance data.
        tools_dir: Directory containing orchestration and utility scripts.
    """

    root: Path
    data_dir: Path
    tools_dir: Path


def get_project_paths() -> ProjectPaths:
    """Resolve and return canonical project directories.

    Infers the project root from the current file location and builds
    consistent paths to data/ and tools/.

    Returns:
        ProjectPaths with root, data_dir, and tools_dir.
    """
    root = Path(__file__).resolve().parents[1]
    return ProjectPaths(root=root, data_dir=root / "data", tools_dir=root / "tools")


def ensure_tools_on_path() -> None:
    """Ensure tools/ is available on sys.path for dynamic imports.

    Allows statements such as `import warehouse_loader` from scripts
    executed outside the tools/ directory.
    """
    tools_dir = get_project_paths().tools_dir
    tools_str = str(tools_dir)
    if tools_str not in sys.path:
        sys.path.insert(0, tools_str)


def resolve_instance_dir(instance_arg: str) -> Path:
    """Resolve an instance argument into an existing directory.

    Supported forms:
        - Alias: toy / market / family / category
        - Explicit path:
            * Absolute path
            * Path relative to project root
            * Path relative to data/

    Resolution order:
        1. Direct existing path.
        2. Alias or raw argument under data/.
        3. Alias or raw argument under project root.

    Args:
        instance_arg: User-provided instance identifier or path.

    Returns:
        Absolute Path to the resolved instance directory.

    Raises:
        FileNotFoundError: If no matching directory is found.
    """
    paths = get_project_paths()

    # Attempt direct path resolution.
    p = Path(instance_arg)
    if p.exists():
        return p.resolve()

    # Translate alias if available.
    rel = ALIASES.get(instance_arg, instance_arg)

    # Try resolution relative to data/.
    candidate = (paths.data_dir / rel).resolve()
    if candidate.exists():
        return candidate

    # Try resolution relative to project root.
    candidate = (paths.root / rel).resolve()
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        f"Instance not found: {instance_arg} (resolved as {rel})"
    )