#!/usr/bin/env python3
from __future__ import annotations

"""
Aisle-based layout (no real coordinates).

Goal:
- Build deterministic 2D positions for each rack using ONLY aisle_racks.txt:
  * each aisle is drawn as 2 columns (left/right)
  * racks alternate left/right: idx 0 -> left, 1 -> right, 2 -> left, ...
  * y increases with the row number inside the aisle
  * aisles are separated along x with a configurable gap

This is the "Step 1" foundation for later:
- static SVG rendering
- interactive click assignment

Usage (from tools/):
    python aisle_layout.py --instance toy
    python aisle_layout.py --instance family --show 20
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from warehouse_loader import WarehouseLoader, WarehouseInstance


# -----------------------------
# Instance path resolution
# -----------------------------

def resolve_instance_dir(instance_arg: str) -> Path:
    """
    Resolve instance directory from:
    - alias: toy / market / family / category
    - or explicit relative path inside data/
    """
    project_root = Path(__file__).resolve().parents[1]
    data_dir = project_root / "data"

    aliases = {
        "toy": data_dir / "warehouse_toy",
        "market": data_dir / "warehouse_big_" / "warehouse_big_market",
        "family": data_dir / "warehouse_big_" / "warehouse_big_family",
        "category": data_dir / "warehouse_big_" / "warehouse_big_category",
    }

    if instance_arg in aliases:
        p = aliases[instance_arg]
    else:
        p = data_dir / instance_arg

    if not p.exists():
        raise FileNotFoundError(f"Instance not found: {p}")
    return p


# -----------------------------
# Layout model
# -----------------------------

@dataclass(frozen=True)
class LayoutCell:
    rack_id: int
    aisle_id: int
    col: int            # 0=left, 1=right
    row: int            # row inside aisle
    x: int              # pixel position (top-left)
    y: int              # pixel position (top-left)


def build_aisle_layout(
    instance: WarehouseInstance,
    cell_px: int = 18,
    col_gap_px: int = 6,
    row_gap_px: int = 2,
    aisle_gap_px: int = 12,
    flip_every_other_aisle: bool = True,
) -> Dict[int, LayoutCell]:
    """
    Build a deterministic grid layout from instance.aisles_racks.

    Rules:
    - Two columns per aisle (left/right), alternating by index.
    - Rows are packed by idx//2.
    - If flip_every_other_aisle: odd aisles are vertically reversed (bottom-up).

    Returns:
        dict rack_id -> LayoutCell
    """
    racks_seen: Dict[int, LayoutCell] = {}
    aisle_width = 2 * cell_px + col_gap_px

    n_racks = int(instance.metadata["num_racks"])
    START, END = 0, n_racks - 1

    for aisle_id, racks in enumerate(instance.aisles_racks):
        # filtre START/END
        racks = [r for r in racks if r not in (START, END)]

        aisle_x0 = aisle_id * (aisle_width + aisle_gap_px)
        n_rows = (len(racks) + 1) // 2

        for idx, rack_id in enumerate(racks):
            col = idx % 2
            row = idx // 2

            if flip_every_other_aisle and (aisle_id % 2 == 1):
                row = (n_rows - 1) - row  # invert vertically

            x = aisle_x0 + col * (cell_px + col_gap_px)
            y = row * (cell_px + row_gap_px)

            cell = LayoutCell(
                rack_id=rack_id,
                aisle_id=aisle_id,
                col=col,
                row=row,
                x=x,
                y=y,
            )

            if rack_id not in racks_seen:
                racks_seen[rack_id] = cell

    return racks_seen



# -----------------------------
# Diagnostics helpers
# -----------------------------

def layout_bbox(layout: Dict[int, LayoutCell], cell_px: int) -> Tuple[int, int]:
    """Return (width, height) in pixels for the whole layout."""
    if not layout:
        return 0, 0
    max_x = max(c.x for c in layout.values())
    max_y = max(c.y for c in layout.values())
    return max_x + cell_px, max_y + cell_px


def find_duplicates(instance: WarehouseInstance) -> List[Tuple[int, List[int]]]:
    """Return list of (rack_id, [aisle_ids]) for racks that appear in multiple aisles."""
    where: Dict[int, List[int]] = {}
    for a, racks in enumerate(instance.aisles_racks):
        for r in racks:
            where.setdefault(r, []).append(a)
    return sorted([(r, aisles) for r, aisles in where.items() if len(aisles) > 1])


def find_missing_racks(instance: WarehouseInstance) -> List[int]:
    """Racks not present in any aisle (excluding start/end often 0 and n-1)."""
    n_racks = int(instance.metadata["num_racks"])
    present = set(r for racks in instance.aisles_racks for r in racks)
    missing = [r for r in range(n_racks) if r not in present]
    return missing


# -----------------------------
# CLI
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True, help="Alias (toy/family/market/category) or path inside data/")
    ap.add_argument("--cell-px", type=int, default=18)
    ap.add_argument("--col-gap-px", type=int, default=6)
    ap.add_argument("--row-gap-px", type=int, default=2)
    ap.add_argument("--aisle-gap-px", type=int, default=12)
    ap.add_argument("--show", type=int, default=12, help="Print first k layout cells (sorted by rack_id)")
    args = ap.parse_args()

    instance_dir = resolve_instance_dir(args.instance)
    loader = WarehouseLoader(str(instance_dir))
    inst = loader.load_all()

    layout = build_aisle_layout(
        inst,
        cell_px=args.cell_px,
        col_gap_px=args.col_gap_px,
        row_gap_px=args.row_gap_px,
        aisle_gap_px=args.aisle_gap_px,
    )

    n_racks = int(inst.metadata["num_racks"])
    n_aisles = int(inst.metadata["num_aisles"])
    w, h = layout_bbox(layout, args.cell_px)

    print(f"[layout] instance={instance_dir.name}")
    print(f"[layout] num_racks={n_racks}  num_aisles={n_aisles}")
    print(f"[layout] placed_racks={len(layout)}  bbox={w}x{h}px")

    dups = find_duplicates(inst)
    if dups:
        print(f"[warn] {len(dups)} racks appear in multiple aisles (showing up to 8): {dups[:8]}")

    missing = find_missing_racks(inst)
    # Often 0 and n-1 are special; keep the warning but show small sample.
    if missing:
        print(f"[warn] {len(missing)} racks missing from aisle_racks (sample): {missing[:12]}")

    k = max(0, args.show)
    if k:
        print(f"\n[first {k} racks by rack_id]")
        for rack_id in sorted(layout.keys())[:k]:
            c = layout[rack_id]
            print(f"rack={c.rack_id:4d}  aisle={c.aisle_id:3d}  col={c.col}  row={c.row:4d}  xy=({c.x},{c.y})")


if __name__ == "__main__":
    main()
