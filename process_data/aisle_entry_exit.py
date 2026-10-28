from __future__ import annotations

from pathlib import Path

from tools.warehouse_loader import WarehouseLoader


def _write_vector(path: Path, vec) -> None:
    """
    Write a numeric vector to disk using the project vector contract format.

    Format:
    - first line: vector length,
    - one float per line thereafter.

    Args:
        path: Destination file path.
        vec: Sequence of numeric values to serialize.
    """
    # Ensure parent directory exists before writing.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(str(len(vec)) + "\n")
        for v in vec:
            f.write(f"{float(v)}\n")


def build_aisle_entry_exit_metrics(instance_dir: Path, out_dir: Path) -> None:
    """
    Compute aisle-level entry/exit accessibility metrics (AMET).

    Definition:
        score(aisle) = dist(start, anchor) + dist(anchor, end)

    where:
        - start = rack 0,
        - end   = rack n-1,
        - anchor = smallest rack index in the aisle,
          excluding special racks {start, end} when possible.

    Additionally:
        - compute a rank vector (0 = best, i.e., smallest score),
        - export both score and rank vectors to the output directory.

    Args:
        instance_dir: Directory containing the warehouse instance.
        out_dir: Destination directory for exported metric files.
    """
    # Load full instance (racks, adjacency, aisle structure).
    inst = WarehouseLoader(str(instance_dir)).load_all()
    m = inst.metadata

    n_racks = m["num_racks"]
    start, end = 0, n_racks - 1
    adj = inst.adjacency  # Rack-to-rack distance matrix.
    aisles = inst.aisles_racks

    scores = []
    for racks in aisles:
        # Exclude special racks (start/end) when selecting the anchor.
        candidates = [r for r in racks if r not in (start, end)]
        anchor = min(candidates) if candidates else min(racks)

        # Compute entry + exit distance through the chosen anchor.
        score = adj[start][anchor] + adj[anchor][end]
        scores.append(float(score))

    # Rank aisles by increasing score (0 = best accessibility).
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    for rk, idx in enumerate(order):
        ranks[idx] = float(rk)

    # Export score and rank vectors using the standard vector format.
    _write_vector(out_dir / "aisle_entry_exit_score.txt", scores)
    _write_vector(out_dir / "aisle_entry_exit_rank.txt", ranks)