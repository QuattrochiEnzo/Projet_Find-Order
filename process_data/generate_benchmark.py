#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import subprocess
import sys
from typing import List, Dict


# Define the minimal file set that identifies a valid instance directory.
REQUIRED_FILES = [
    "metadata.txt",
    "rack_capacity.txt",
    "rack_adjacency_matrix.txt",
    "aisle_racks.txt",
    "product_circuit.txt",
    "orders.txt",
]


def is_instance_dir(d: Path) -> bool:
    """
    Check whether a directory matches the expected instance file contract.

    Args:
        d: Candidate directory path.

    Returns:
        True if the directory exists and contains all REQUIRED_FILES, else False.
    """
    return d.is_dir() and all((d / f).is_file() for f in REQUIRED_FILES)


def find_instance_dirs(data_dir: Path) -> List[Path]:
    """
    Discover all instance directories under a given data root.

    Args:
        data_dir: Root directory to recursively scan.

    Returns:
        A sorted list of directories that satisfy is_instance_dir().
    """
    return sorted([p for p in data_dir.rglob("*") if is_instance_dir(p)])


def safe_name_from_path(p: Path, data_dir: Path) -> str:
    """
    Build a filesystem-safe stable name from a path relative to the data root.

    Args:
        p: Instance directory path.
        data_dir: Data root used to compute relative parts.

    Returns:
        A string composed by joining relative path parts with "__".
    """
    rel = p.relative_to(data_dir)
    return "__".join(rel.parts)


def parse_int_list(s: str) -> List[int]:
    """
    Parse a comma-separated list of integers.

    Args:
        s: Input string (e.g., "10,15,20").

    Returns:
        List of parsed integers (empty if input is empty/blank).
    """
    s = s.strip()
    if not s:
        return []
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def parse_str_list(s: str) -> List[str]:
    """
    Parse a comma-separated list of strings.

    Args:
        s: Input string (e.g., "top,stratified").

    Returns:
        List of parsed tokens (empty if input is empty/blank).
    """
    s = s.strip()
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def apply_profile_defaults(args: argparse.Namespace) -> None:
    """
    Apply profile presets to reduce repetitive CLI configuration.

    Rules:
    - A profile provides default grids and modes.
    - Explicit user-provided options always take precedence.

    Args:
        args: Parsed argparse namespace to mutate in-place.
    """
    prof = (args.profile or "").strip().lower()
    if not prof:
        return

    presets: Dict[str, Dict[str, str]] = {
        "toy++": {
            "products": "10,15,20,25",
            "max_racks": "20,30,40,50",
            "max_orders": "25,40,60,80",
            "max_circuits": "2,3,4",
            "selects": "top,stratified",
            "order_modes": "intersect",
            "min_order_size": "2",
            "order_limit": "largest",
            "keep_endpoints": "1",
            "seed": "0",
        },
        "small": {
            "products": "20,25,30,35,40",
            "max_racks": "20,30,40,50",
            "max_orders": "25,40,60,80",
            "max_circuits": "2,3,4,5",
            "selects": "top,stratified",
            "order_modes": "intersect",
            "min_order_size": "2",
            "order_limit": "largest",
            "keep_endpoints": "1",
            "seed": "0",
        },
    }

    if prof not in presets:
        raise SystemExit(f"Unknown --profile '{args.profile}'. Supported: {', '.join(sorted(presets.keys()))}")

    P = presets[prof]

    def set_if_empty(attr: str, value: str) -> None:
        cur = getattr(args, attr)
        if cur is None or (isinstance(cur, str) and cur.strip() == ""):
            setattr(args, attr, value)

    # Fill grid and mode defaults only when corresponding CLI fields are empty.
    set_if_empty("products", P["products"])
    set_if_empty("max_racks", P["max_racks"])
    set_if_empty("max_orders", P["max_orders"])
    set_if_empty("max_circuits", P["max_circuits"])
    set_if_empty("selects", P["selects"])
    set_if_empty("order_modes", P["order_modes"])

    # Fill scalar defaults only when the user did not provide values explicitly.
    if args.min_order_size is None:
        args.min_order_size = int(P["min_order_size"])
    if args.order_limit is None:
        args.order_limit = P["order_limit"]
    if args.seed is None:
        args.seed = int(P["seed"])
    if args.keep_endpoints is None:
        args.keep_endpoints = (P["keep_endpoints"] == "1")


def run_make_subinstance(
    src_dir: Path,
    out_dir: Path,
    n_products: int,
    select: str,
    order_mode: str,
    min_order_size: int,
    max_orders: int,
    order_limit: str,
    max_racks: int,
    keep_endpoints: bool,
    max_circuits: int,
    seed: int,
) -> None:
    """
    Invoke the sub-instance generator as a subprocess with the requested configuration.

    Args:
        src_dir: Base instance directory.
        out_dir: Output directory where the sub-instance is written.
        n_products: Target number of products in the sub-instance.
        select: Product selection strategy.
        order_mode: Order filtering mode.
        min_order_size: Minimum retained order size.
        max_orders: Maximum number of retained orders.
        order_limit: Strategy used when trimming orders ("first" | "random" | "largest").
        max_racks: Maximum number of racks kept in the sub-instance.
        keep_endpoints: Whether to preserve special racks (0 and R-1) when possible.
        max_circuits: Maximum number of circuits kept in the sub-instance.
        seed: RNG seed for reproducible sampling.
    """
    cmd = [
        sys.executable,
        "-m",
        "process_data.make_subinstance",
        "--instance-dir",
        str(src_dir),
        "--out-dir",
        str(out_dir),
        "--n-products",
        str(n_products),
        "--select",
        select,
        "--order-mode",
        order_mode,
        "--min-order-size",
        str(min_order_size),
        "--max-orders",
        str(max_orders),
        "--order-limit",
        order_limit,
        "--max-racks",
        str(max_racks),
        "--seed",
        str(seed),
        "--max-circuits",
        str(max_circuits),
    ]
    if keep_endpoints:
        cmd.append("--keep-endpoints")

    subprocess.run(cmd, check=True)


def main() -> None:
    """
    Generate a catalog of MILP-friendly sub-instances across all detected base instances.

    Workflow:
    - scan a data root for base instances matching REQUIRED_FILES,
    - optionally limit the number of processed bases,
    - expand a grid of (P, Rmax, Kmax, Cmax, select, order_mode),
    - run the generator for each configuration,
    - write a CSV catalog listing all generated sub-instances and parameters.
    """
    ap = argparse.ArgumentParser(description="Generate a benchmark of MILP-friendly sub-instances from all bases in data/.")

    ap.add_argument("--data-dir", default="data", help="Répertoire data/ du projet")
    ap.add_argument("--out-root", default="data/bench_milp", help="Répertoire racine des sous-instances")
    ap.add_argument("--max-bases", type=int, default=0, help="Limiter le nb de bases (0 = toutes)")

    # New: presets.
    ap.add_argument("--profile", default="", help="Preset: toy++ | small")

    # New: grids.
    ap.add_argument("--products", default="", help="Liste P' (ex: 10,15,20)")
    ap.add_argument("--max-racks", default="", help="Liste R' max (ex: 20,30,40)")
    ap.add_argument("--max-orders", default="", help="Liste K' max (ex: 25,40,60)")
    ap.add_argument("--max-circuits", default="", help="Liste C' max (ex: 2,3,4)")

    # Selection / orders.
    ap.add_argument("--selects", default="", help="top,stratified")
    ap.add_argument("--order-modes", default="", help="strict,intersect")
    ap.add_argument("--min-order-size", type=int, default=None, help="min-order-size (utile surtout pour intersect)")
    ap.add_argument("--order-limit", choices=["first", "random", "largest"], default=None, help="Stratégie limitation commandes")

    # Racks / reproducibility.
    ap.add_argument("--keep-endpoints", action="store_true", help="Garder racks 0 et (R-1) si possible")
    ap.add_argument("--seed", type=int, default=None)

    args = ap.parse_args()
    apply_profile_defaults(args)

    project_root = Path(__file__).resolve().parents[1]
    data_dir = (project_root / args.data_dir).resolve()
    out_root = (project_root / args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    # Discover base instances, excluding any toy directories from the benchmark pool.
    bases = find_instance_dirs(data_dir)
    bases = [b for b in bases if "toy" not in b.name.lower() and "toy" not in str(b).lower()]
    if args.max_bases and args.max_bases > 0:
        bases = bases[: args.max_bases]

    if not bases:
        raise SystemExit(f"Aucune instance trouvée sous {data_dir} (attendu: {REQUIRED_FILES})")

    # Parse grid parameters.
    products = parse_int_list(args.products)
    max_racks_list = parse_int_list(args.max_racks)
    max_orders_list = parse_int_list(args.max_orders)
    max_circuits_list = parse_int_list(args.max_circuits)

    selects = parse_str_list(args.selects)
    order_modes = parse_str_list(args.order_modes)

    # Enforce required grids (or a profile).
    if not products:
        raise SystemExit("Missing --products (ex: 10,15,20) or use --profile toy++")
    if not max_racks_list:
        raise SystemExit("Missing --max-racks or use --profile toy++")
    if not max_orders_list:
        raise SystemExit("Missing --max-orders or use --profile toy++")
    if not max_circuits_list:
        raise SystemExit("Missing --max-circuits or use --profile toy++")
    if not selects:
        raise SystemExit("Missing --selects or use --profile toy++")
    if not order_modes:
        raise SystemExit("Missing --order-modes or use --profile toy++")

    # Normalize scalar parameters with stable defaults.
    min_order_size = int(args.min_order_size if args.min_order_size is not None else 2)
    order_limit = args.order_limit or "largest"
    seed = int(args.seed if args.seed is not None else 0)
    keep_endpoints = bool(args.keep_endpoints)

    # Write a catalog that can be consumed by downstream benchmark runners.
    catalog_csv = out_root / "benchmark_instances.csv"
    with catalog_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "base_dir",
            "sub_dir",
            "P",
            "Rmax",
            "Kmax",
            "Cmax",
            "select",
            "order_mode",
            "min_order_size",
            "order_limit",
            "keep_endpoints",
            "seed",
        ])

        for base_dir in bases:
            base_name = safe_name_from_path(base_dir, data_dir)

            # Expand full grid and generate each sub-instance deterministically.
            for P in products:
                for Rmax in max_racks_list:
                    for Kmax in max_orders_list:
                        for Cmax in max_circuits_list:
                            for sel in selects:
                                for om in order_modes:
                                    m = min_order_size if om == "intersect" else 1

                                    sub_name = (
                                        f"sub__{base_name}"
                                        f"__P{P}__R{Rmax}__K{Kmax}__C{Cmax}"
                                        f"__sel-{sel}__ord-{om}__m{m}"
                                        f"__limit-{order_limit}"
                                        f"__end-{int(keep_endpoints)}__seed{seed}"
                                    )
                                    sub_dir = out_root / sub_name

                                    print(f"[GEN] base={base_name} -> {sub_name}")
                                    run_make_subinstance(
                                        src_dir=base_dir,
                                        out_dir=sub_dir,
                                        n_products=P,
                                        select=sel,
                                        order_mode=om,
                                        min_order_size=m,
                                        max_orders=Kmax,
                                        order_limit=order_limit,
                                        max_racks=Rmax,
                                        keep_endpoints=keep_endpoints,
                                        max_circuits=Cmax,
                                        seed=seed,
                                    )

                                    w.writerow([
                                        str(base_dir),
                                        str(sub_dir),
                                        P,
                                        Rmax,
                                        Kmax,
                                        Cmax,
                                        sel,
                                        om,
                                        m,
                                        order_limit,
                                        int(keep_endpoints),
                                        seed,
                                    ])

    print("[OK] catalogue écrit:", catalog_csv)


if __name__ == "__main__":
    main()