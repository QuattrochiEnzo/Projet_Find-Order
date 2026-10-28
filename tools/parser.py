#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Any
import argparse


# ============================================================
# Project paths
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent                                                       # repository root (one level above tools/)
DATA_DIR = PROJECT_ROOT / "data"                                                                            # canonical data directory


# ============================================================
# Instance aliases (adapted to this repository layout)
# ============================================================

def _first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:                                                                                          # scan candidates in order
        if p.exists():                                                                                       # return the first existing path
            return p                                                                                          # stop at first match
    return None                                                                                               # no candidate exists


def resolve_instance_path(user_value: str | Path) -> Path:
    """
    Resolve --instance into an instance directory.

    Resolution order:
    - Alias: toy | market | family | category
    - Relative to data/ (DATA_DIR / <value>)
    - Relative to current working directory (cwd / <value>)
    - Absolute path (if it exists)
    """
    s = str(user_value).strip()                                                                              # normalize user input
    key = s.lower()                                                                                          # case-insensitive alias matching

    # -------- aliases --------
    if key == "toy":
        p = _first_existing([
            DATA_DIR / "warehouse_toy",                                                                      # primary toy location
            DATA_DIR / "warehouse_toy",                                                                      # duplicate kept for backward compatibility
        ])
        if p:
            return p.resolve()                                                                               # canonical absolute path
        raise FileNotFoundError("Alias 'toy' not found under data/")                                         # English error message

    if key in ("market", "family", "category"):
        subs = {
            "market": "warehouse_big_market",
            "family": "warehouse_big_family",
            "category": "warehouse_big_category",
        }[key]                                                                                                # map alias -> subfolder name

        candidates = []                                                                                       # build candidate paths
        for root in ["warehouse_big_", "warehouse_big_"]:
            candidates.append(DATA_DIR / root / subs)                                                         # normal candidate
            candidates.append(DATA_DIR / root / subs.replace("wharehouse", "warehouse", 1))                   # tolerate common typo variant

        p = _first_existing(candidates)                                                                       # pick first existing candidate
        if p:
            return p.resolve()                                                                                # canonical absolute path
        raise FileNotFoundError(f"Alias '{key}' not found under data/")                                       # English error message

    # -------- normal paths --------
    p = Path(s)                                                                                               # interpret as filesystem path

    if p.is_absolute() and p.exists():
        return p.resolve()                                                                                    # accept existing absolute path

    p_data = DATA_DIR / p                                                                                     # try relative to data/
    if p_data.exists():
        return p_data.resolve()                                                                               # accept existing data-relative path

    p_cwd = Path.cwd() / p                                                                                    # try relative to cwd
    if p_cwd.exists():
        return p_cwd.resolve()                                                                                # accept existing cwd-relative path

    raise FileNotFoundError(f"Instance not found: {s}")                                                       # English error message


# ============================================================
# Instance directory detection
# ============================================================

REQUIRED_FILES = [
    "metadata.txt",
    "rack_capacity.txt",
    "rack_adjacency_matrix.txt",
    "product_circuit.txt",
    "aisle_racks.txt",
    "orders.txt",
]                                                                                                             # minimal file set for a valid instance


def is_instance_dir(p: Path) -> bool:
    return p.is_dir() and all((p / f).is_file() for f in REQUIRED_FILES)                                      # validate directory + required files


# ============================================================
# File reading utilities
# ============================================================

def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")                                                      # English error message
    with path.open("r", encoding="utf-8") as f:                                                               # open text file (UTF-8)
        return [l.strip() for l in f if l.strip()]                                                            # strip and drop empty lines


def _read_int_lines(path: Path) -> List[int]:
    return [int(x) for x in _read_lines(path)]                                                                # parse one int per non-empty line


def _read_counted_int_list(path: Path, expected_n: int) -> List[int]:
    lines = _read_int_lines(path)                                                                             # parse all ints from file
    if not lines:
        raise ValueError(f"Empty file: {path}")                                                               # English error message
    declared = lines[0]                                                                                       # first value is declared length
    if declared != expected_n:
        raise ValueError(f"{path.name}: expected {expected_n}, got {declared}")                               # English error message
    return lines[1:]                                                                                          # return payload values


def _read_int_matrix(path: Path) -> List[List[int]]:
    lines = _read_lines(path)                                                                                 # read non-empty lines
    n = int(lines[0])                                                                                         # declared matrix size
    mat = []                                                                                                  # accumulate rows
    for i in range(1, 1 + n):                                                                                 # read exactly n rows
        row = [int(x) for x in lines[i].split()]                                                              # parse row values
        if len(row) != n:
            raise ValueError(f"Invalid matrix (row {i}): {path}")                                             # English error message
        mat.append(row)                                                                                       # store parsed row
    return mat                                                                                                # return adjacency matrix


# ============================================================
# Data classes
# ============================================================

@dataclass
class Metadata:
    n_racks: int
    total_locations: int
    aeration_pct: int
    n_products: int
    n_circuits: int
    n_aisles: int
    n_orders: int                                                                                             # raw metadata fields (as provided)


@dataclass
class Instance:
    root: Path
    metadata: Metadata
    rack_capacity: List[int]
    adjacency: List[List[int]]
    product_circuit: List[int]
    aisle_racks: List[List[int]]
    orders: List[List[int]]
    rack_coordinates: Optional[List[List[float]]] = None
    aisle_directions: Optional[List[int]] = None
    rack_product_assignment: Optional[List[int]] = None                                                      # optional solution vector


# ============================================================
# Parsers
# ============================================================

def parse_metadata(p: Path) -> Metadata:
    l = _read_int_lines(p)                                                                                    # read 7 scalar lines
    if len(l) != 7:
        raise ValueError(f"{p.name}: expected 7 lines, got {len(l)}")                                         # English error message
    return Metadata(*l)                                                                                       # positional mapping to dataclass


def parse_rack_capacity(p: Path, n: int) -> List[int]:
    return _read_counted_int_list(p, n)                                                                       # parse counted list of rack capacities


def parse_product_circuit(p: Path, n: int) -> List[int]:
    return _read_counted_int_list(p, n)                                                                       # parse counted list of product circuits


def parse_aisle_racks(p: Path, n: int) -> List[List[int]]:
    lines = _read_lines(p)                                                                                    # read non-empty lines
    if int(lines[0]) != n:
        raise ValueError(f"{p.name}: expected {n}, got {lines[0]}")                                           # English error message
    aisles = []                                                                                               # accumulate aisle rack lists
    for i in range(1, n + 1):                                                                                 # parse each aisle line
        parts = list(map(int, lines[i].split()))                                                              # token -> int
        k, racks = parts[0], parts[1:]                                                                        # k + rack ids
        if k != len(racks):
            raise ValueError(f"{p.name} line {i}: k={k} but {len(racks)} racks")                              # English error message
        aisles.append(racks)                                                                                  # store aisle definition
    return aisles                                                                                              # return aisle list


def parse_orders(p: Path, n: int) -> List[List[int]]:
    lines = _read_lines(p)                                                                                    # read non-empty lines
    if int(lines[0]) != n:
        raise ValueError(f"{p.name}: expected {n}, got {lines[0]}")                                           # English error message
    orders = []                                                                                                # accumulate orders
    for i in range(1, n + 1):                                                                                 # parse each order line
        parts = list(map(int, lines[i].split()))                                                              # token -> int
        k, prods = parts[0], parts[1:]                                                                        # k + product ids
        if k != len(prods):
            raise ValueError(f"{p.name} line {i}: k={k} but {len(prods)} ids")                                # English error message
        orders.append(prods)                                                                                  # store order product list
    return orders                                                                                              # return all orders


def parse_solution(p: Path, n_products: int) -> List[int]:
    return _read_counted_int_list(p, n_products)                                                              # parse counted list of rack assignments


# ============================================================
# Instance loader
# ============================================================

def load_instance(path: Path, load_help=True, load_solution=False) -> Instance:
    if not is_instance_dir(path):
        raise ValueError(f"Not a valid instance directory: {path}")                                          # English error message

    m = parse_metadata(path / "metadata.txt")                                                                 # parse instance metadata

    adjacency = _read_int_matrix(path / "rack_adjacency_matrix.txt")                                          # parse adjacency matrix
    if len(adjacency) != m.n_racks:
        print(f"(warning) adjacency: {len(adjacency)} nodes, metadata.n_racks={m.n_racks}")                   # warn (tolerate spec mismatch)

    inst = Instance(
        root=path,
        metadata=m,
        rack_capacity=parse_rack_capacity(path / "rack_capacity.txt", m.n_racks),
        adjacency=adjacency,
        product_circuit=parse_product_circuit(path / "product_circuit.txt", m.n_products),
        aisle_racks=parse_aisle_racks(path / "aisle_racks.txt", m.n_aisles),
        orders=parse_orders(path / "orders.txt", m.n_orders),
    )                                                                                                         # load core instance data

    if load_help:
        coords_path = path / "help/rack_coordinates.txt"                                                      # optional geometry file
        if coords_path.exists():
            coords_lines = _read_lines(coords_path)                                                          # read coordinates lines
            declared = int(coords_lines[0])                                                                  # declared rack count
            if declared != m.n_racks:
                raise ValueError(f"{coords_path.name}: expected {m.n_racks}, got {declared}")                # English error message
            inst.rack_coordinates = [
                list(map(float, l.split()))
                for l in coords_lines[1:]
            ]                                                                                                 # parse x/y coordinates

        dirs_path = path / "help/aisle_directions.txt"                                                        # optional direction file
        if dirs_path.exists():
            dirs = _read_int_lines(dirs_path)                                                                 # parse direction codes
            declared = dirs[0]                                                                                # declared aisle count
            if declared != m.n_aisles:
                raise ValueError(f"{dirs_path.name}: expected {m.n_aisles}, got {declared}")                 # English error message
            inst.aisle_directions = dirs[1:]                                                                  # store aisle directions

    if load_solution:
        sol_path = path / "solutions/rack_product_assignment.txt"                                             # default solution path
        if sol_path.exists():
            inst.rack_product_assignment = parse_solution(sol_path, m.n_products)                            # parse solution vector

    return inst                                                                                               # return fully loaded instance


# ============================================================
# Printing helpers
# ============================================================

def print_min(inst: Instance):
    m = inst.metadata                                                                                         # shorthand for metadata
    print(f"[Instance] {inst.root.name}")                                                                     # instance identifier
    print(f"  racks={m.n_racks}, products={m.n_products}, orders={m.n_orders}")                               # minimal summary line (English)


def _print_list_preview(values: List[Any], limit: int):
    shown = values[:limit]                                                                                    # preview slice
    print(shown)                                                                                              # print preview
    if len(values) > limit:
        print(f"(showing {limit}/{len(values)} — use --limit)")                                               # truncation hint (English)


def print_block(inst: Instance, name: str, limit: int):
    name = name.lower()                                                                                       # normalize block name

    if name == "metadata":
        print(inst.metadata)                                                                                  # print dataclass

    elif name == "orders":
        shown = inst.orders[:limit]                                                                           # preview first orders
        for i, o in enumerate(shown):
            print(f"order {i}: {o}")                                                                          # per-order print (English)
        if len(inst.orders) > limit:
            print(f"(showing {limit}/{len(inst.orders)} — use --limit)")                                      # truncation hint (English)

    elif name == "product_circuit":
        _print_list_preview(inst.product_circuit, limit)                                                      # preview product->circuit mapping

    elif name == "rack_capacity":
        _print_list_preview(inst.rack_capacity, limit)                                                        # preview rack capacities

    elif name in ("solution", "rack_product_assignment"):
        if inst.rack_product_assignment is None:
            print("(no solution loaded — add --solution)")                                                    # hint to enable loading (English)
        else:
            _print_list_preview(inst.rack_product_assignment, limit)                                          # preview solution vector

    else:
        print(f"Unknown block: {name}")                                                                       # unknown block name (English)


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser()                                                                            # CLI argument parser
    ap.add_argument("--instance", required=True,
                    help="toy | market | family | category | or a path")                                     # CLI help (English)
    ap.add_argument("--show", nargs="*", default=[],
                    help="Blocks: metadata rack_capacity product_circuit orders solution")                   # CLI help (English)
    ap.add_argument("--limit", type=int, default=10,
                    help="Preview limit (per block)")                                                        # CLI help (English)
    ap.add_argument("--min", action="store_true",
                    help="Print minimal summary")                                                             # CLI help (English)
    ap.add_argument("--solution", action="store_true",
                    help="Load solution from solutions/")                                                    # CLI help (English)
    ap.add_argument("--no-help", action="store_true",
                    help="Do not try to load help/")                                                         # CLI help (English)

    args = ap.parse_args()                                                                                   # parse argv

    inst_path = resolve_instance_path(args.instance)                                                          # resolve instance directory
    inst = load_instance(
        inst_path,
        load_help=not args.no_help,
        load_solution=args.solution
    )                                                                                                        # load instance from disk

    if args.min or not args.show:
        print_min(inst)                                                                                      # default output is minimal summary

    for b in args.show:
        print(f"\n=== {b} ===")                                                                              # block header
        print_block(inst, b, args.limit)                                                                     # print requested block


if __name__ == "__main__":
    main()                                                                                                   # CLI entrypoint