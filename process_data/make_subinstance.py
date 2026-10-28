#!/usr/bin/env python3
from __future__ import annotations

"""
make_subinstance.py

Generate a "MILP-solvable" sub-instance by reducing:
- the number of products (P')
- the number of orders (K')
- and, critically, the number of racks (R') while preserving a coherent aisle structure.

Rack strategy (compact, MILP-friendly):
- optionally keep special racks (0 and num_racks-1) when present,
- keep a subset of racks by retaining ~target_per_aisle racks per aisle
  (e.g., R'=20 -> 2 aisles of 10 racks instead of 20 aisles of 1 rack),
- remap racks to 0..R'-1 and rebuild:
  - rack_capacity.txt
  - rack_adjacency_matrix.txt (sub-matrix)
  - aisle_racks.txt (with remapped rack ids)
  - metadata.txt (num_racks, total_slots, num_orders, num_products)
- remap products to 0..P'-1,
- filter and cap orders (max_orders).

Note:
- Circuits are remapped to 0..C'-1 (clean for learning and for toy++ presets).
"""

import sys
from pathlib import Path

# Ensure the repository root is in PYTHONPATH.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.warehouse_loader import WarehouseLoader  # type: ignore


import argparse
import math
import random
import shutil
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

# Robust import depending on current working directory.
try:
    from tools.warehouse_loader import WarehouseLoader  # type: ignore
except Exception:  # pragma: no cover
    from warehouse_loader import WarehouseLoader  # type: ignore


# -----------------------------
# Popularity & product selection
# -----------------------------

def compute_popularity(orders: List[List[int]], n_products: int) -> List[int]:
    """
    Compute per-product popularity as "number of orders containing the product".

    Args:
        orders: Orders expressed as lists of product ids.
        n_products: Total number of products in the original instance.

    Returns:
        Popularity vector of length n_products.
    """
    pop = [0] * n_products
    for order in orders:
        for p in set(order):
            if 0 <= p < n_products:
                pop[p] += 1
    return pop


def select_top_products(popularity: List[int], target_n: int) -> List[int]:
    """
    Select the top-N products by popularity.

    Args:
        popularity: Per-product popularity scores.
        target_n: Number of products to keep.

    Returns:
        List of selected original product ids.
    """
    ranked = sorted(range(len(popularity)), key=lambda p: (popularity[p], -p), reverse=True)
    return ranked[:target_n]


def select_stratified_by_circuit(
    product_circuit: List[int],
    popularity: List[int],
    target_n: int,
) -> List[int]:
    """
    Select products while preserving circuit proportions.

    Strategy:
    - compute circuit sizes,
    - allocate a target quota per circuit proportional to its size,
    - pick the most popular products within each circuit,
    - backfill if quotas underfill due to rounding.

    Args:
        product_circuit: Mapping product -> circuit id.
        popularity: Per-product popularity scores.
        target_n: Number of products to keep.

    Returns:
        List of selected original product ids.
    """
    by_circuit: Dict[int, List[int]] = defaultdict(list)
    for p, c in enumerate(product_circuit):
        by_circuit[int(c)].append(p)

    circuits = sorted(by_circuit.keys())
    sizes = {c: len(by_circuit[c]) for c in circuits}
    total = sum(sizes.values()) if circuits else 0
    if total == 0:
        return []

    quota = {c: max(0, round(target_n * sizes[c] / total)) for c in circuits}
    picked = sum(quota.values())

    if picked != target_n:
        order_c = sorted(circuits, key=lambda c: sizes[c], reverse=True)
        i = 0
        while picked < target_n:
            c = order_c[i % len(order_c)]
            quota[c] += 1
            picked += 1
            i += 1
        i = 0
        while picked > target_n:
            c = order_c[i % len(order_c)]
            if quota[c] > 0:
                quota[c] -= 1
                picked -= 1
            i += 1

    selected: List[int] = []
    for c in circuits:
        prods = by_circuit[c]
        prods_sorted = sorted(prods, key=lambda p: (popularity[p], -p), reverse=True)
        selected.extend(prods_sorted[: quota[c]])

    if len(selected) < target_n:
        sel = set(selected)
        rest = [p for p in range(len(product_circuit)) if p not in sel]
        rest_sorted = sorted(rest, key=lambda p: (popularity[p], -p), reverse=True)
        selected.extend(rest_sorted[: (target_n - len(selected))])

    return selected[:target_n]


# -----------------------------
# Remap products + orders
# -----------------------------

def build_mapping(selected_old: List[int]) -> Dict[int, int]:
    """
    Build a dense product id mapping old_id -> new_id.

    Args:
        selected_old: Selected original product ids.

    Returns:
        Mapping from original ids to new contiguous ids.
    """
    return {old: new for new, old in enumerate(selected_old)}


def filter_and_remap_orders(
    orders_old: List[List[int]],
    mapping: Dict[int, int],
    mode: str,
    min_size: int,
) -> List[List[int]]:
    """
    Filter and remap orders under the selected product set.

    Modes:
    - strict: keep only orders fully contained in the selected set,
    - intersect: keep intersection with selected set if large enough.

    Args:
        orders_old: Original orders as lists of product ids.
        mapping: Product id mapping old_id -> new_id.
        mode: Filtering mode ("strict" | "intersect").
        min_size: Minimum retained order size after filtering.

    Returns:
        New orders expressed in remapped product ids.
    """
    selected_set = set(mapping.keys())
    out: List[List[int]] = []

    for order in orders_old:
        if mode == "strict":
            if all(p in selected_set for p in order):
                remapped = [mapping[p] for p in order]
                if len(remapped) >= min_size:
                    out.append(remapped)
        elif mode == "intersect":
            inter = [p for p in order if p in selected_set]
            if len(inter) >= min_size:
                remapped = [mapping[p] for p in inter]
                out.append(remapped)
        else:
            raise ValueError(f"unknown order mode: {mode}")

    return out


def limit_orders(
    orders: List[List[int]],
    max_orders: int,
    rng: random.Random,
    strategy: str,
) -> List[List[int]]:
    """
    Limit the number of orders using a chosen trimming strategy.

    Strategies:
    - first: keep the first max_orders orders,
    - random: sample max_orders orders uniformly (seeded),
    - largest: keep the longest orders (ties broken deterministically).

    Args:
        orders: Candidate orders after filtering/remapping.
        max_orders: Maximum number of orders to retain (<=0 means no limit).
        rng: Random generator used for "random" strategy.
        strategy: Trimming strategy ("first" | "random" | "largest").

    Returns:
        List of retained orders.
    """
    if max_orders <= 0 or len(orders) <= max_orders:
        return orders

    if strategy == "first":
        return orders[:max_orders]

    if strategy == "random":
        idx = list(range(len(orders)))
        rng.shuffle(idx)
        idx = idx[:max_orders]
        return [orders[i] for i in idx]

    if strategy == "largest":
        return sorted(orders, key=lambda o: (len(o), o), reverse=True)[:max_orders]

    raise ValueError(f"unknown order-limit strategy: {strategy}")


# -----------------------------
# Racks: selection + remap
# -----------------------------

def pick_racks_compact(
    aisles_racks: List[List[int]],
    num_racks: int,
    max_racks: int,
    keep_endpoints: bool,
    target_per_aisle: int,
) -> List[int]:
    """
    Select racks using a compact aisle-preserving policy.

    Policy:
    - keep ~target_per_aisle racks per selected aisle,
    - therefore for max_racks=20 and target_per_aisle=10 -> 2 aisles of 10 racks,
    - avoid spreading 1 rack per aisle across many aisles (MILP-unfriendly fragmentation).

    Args:
        aisles_racks: Aisle structure as lists of rack ids.
        num_racks: Total number of racks in the original instance.
        max_racks: Maximum number of racks to keep (<=0 means keep all).
        keep_endpoints: Whether to force keeping racks 0 and (num_racks-1) when possible.
        target_per_aisle: Target racks per aisle in the reduced instance.

    Returns:
        Sorted list of retained original rack ids.
    """
    if max_racks <= 0 or max_racks >= num_racks:
        return list(range(num_racks))

    must: Set[int] = set()
    if keep_endpoints and num_racks >= 2:
        must.add(0)
        must.add(num_racks - 1)

    A_total = len(aisles_racks)
    if A_total == 0:
        racks = list(must)
        for r in range(num_racks):
            if r not in must:
                racks.append(r)
            if len(racks) >= max_racks:
                break
        return sorted(set(racks))

    # Compute how many aisles to keep to reach ~target_per_aisle racks/aisle.
    tpa = max(1, int(target_per_aisle))
    A_keep = min(A_total, max(1, math.ceil(max_racks / tpa)))

    # Pick the aisles with the most valid racks (stable and simple heuristic).
    def valid_count(a: int) -> int:
        return sum(1 for r in aisles_racks[a] if 0 <= r < num_racks)

    chosen_aisles = sorted(range(A_total), key=valid_count, reverse=True)[:A_keep]

    # Allocate rack quotas across selected aisles.
    rem = max_racks - len(must)
    if rem <= 0:
        return sorted(must)[:max_racks]

    base = rem // A_keep
    extra = rem % A_keep

    picked: Set[int] = set(must)

    for i, a in enumerate(chosen_aisles):
        q = base + (1 if i < extra else 0)
        if q <= 0:
            continue
        added = 0
        for r in aisles_racks[a]:
            if not (0 <= r < num_racks):
                continue
            if r in picked:
                continue
            picked.add(r)
            added += 1
            if added >= q or len(picked) >= max_racks:
                break
        if len(picked) >= max_racks:
            break

    # Backfill when quotas underfill due to small aisles or excluded endpoints.
    if len(picked) < max_racks:
        # First: continue within chosen aisles.
        for a in chosen_aisles:
            for r in aisles_racks[a]:
                if 0 <= r < num_racks and r not in picked:
                    picked.add(r)
                    if len(picked) >= max_racks:
                        break
            if len(picked) >= max_racks:
                break

    if len(picked) < max_racks:
        # Then: global fallback scan.
        for r in range(num_racks):
            if r not in picked:
                picked.add(r)
            if len(picked) >= max_racks:
                break

    return sorted(picked)


def remap_racks(
    racks_kept_old: List[int],
    rack_capacity_old: List[int],
    adjacency_old: List[List[int]],
    aisles_racks_old: List[List[int]],
) -> Tuple[Dict[int, int], List[int], List[List[int]], List[List[int]]]:
    """
    Remap retained racks into a dense 0..R'-1 index space and rebuild derived structures.

    Returns:
        rack_map: old -> new
        rack_capacity_new: capacities for retained racks
        adjacency_new: induced sub-matrix over retained racks
        aisles_racks_new: aisles with remapped ids (empty aisles removed)

    Args:
        racks_kept_old: Sorted list of retained original rack ids.
        rack_capacity_old: Original rack capacities.
        adjacency_old: Original rack-to-rack distance matrix.
        aisles_racks_old: Original aisle structure (rack ids).
    """
    rack_map: Dict[int, int] = {r_old: i for i, r_old in enumerate(racks_kept_old)}
    rack_capacity_new = [rack_capacity_old[r_old] for r_old in racks_kept_old]

    adjacency_new: List[List[int]] = []
    for r_i in racks_kept_old:
        adjacency_new.append([adjacency_old[r_i][r_j] for r_j in racks_kept_old])

    kept_set = set(racks_kept_old)
    aisles_racks_new: List[List[int]] = []
    for racks_a in aisles_racks_old:
        new_list = [rack_map[r] for r in racks_a if r in kept_set]
        if len(new_list) > 0:
            aisles_racks_new.append(new_list)

    return rack_map, rack_capacity_new, adjacency_new, aisles_racks_new


# -----------------------------
# Instance file IO
# -----------------------------

def write_metadata(
    out_path: Path,
    meta: Dict[str, float],
    new_num_racks: int,
    new_total_slots: int,
    new_num_products: int,
    new_num_orders: int,
    new_num_aisles: int,
    new_num_circuits: int,
) -> None:
    """
    Write metadata.txt for the generated sub-instance.

    The output follows the existing metadata contract order and replaces only
    fields that change with the sub-instance reduction.

    Args:
        out_path: Destination metadata file path.
        meta: Source metadata mapping from the base instance.
        new_num_racks: R' number of racks.
        new_total_slots: Sum of capacities over retained racks.
        new_num_products: P' number of products.
        new_num_orders: K' number of orders.
        new_num_aisles: Number of non-empty aisles after rack selection.
        new_num_circuits: C' number of circuits after remapping.
    """
    keys = ["num_racks", "total_slots", "aeration_rate", "num_products", "num_circuits", "num_aisles", "num_orders"]
    meta2 = dict(meta)
    meta2["num_racks"] = int(new_num_racks)
    meta2["total_slots"] = int(new_total_slots)
    meta2["num_products"] = int(new_num_products)
    meta2["num_orders"] = int(new_num_orders)
    meta2["num_aisles"] = int(new_num_aisles)
    meta2["num_circuits"] = int(new_num_circuits)

    lines: List[str] = []
    for k in keys:
        v = meta2[k]
        if k == "aeration_rate":
            lines.append(str(float(v)))
        else:
            lines.append(str(int(v)))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_rack_capacity(out_path: Path, rack_capacity: List[int]) -> None:
    """
    Write rack_capacity.txt for the sub-instance.

    Args:
        out_path: Destination file path.
        rack_capacity: Capacity vector indexed by new rack ids.
    """
    lines = [str(len(rack_capacity))] + [str(int(x)) for x in rack_capacity]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_adjacency(out_path: Path, adjacency: List[List[int]]) -> None:
    """
    Write rack_adjacency_matrix.txt for the sub-instance.

    Args:
        out_path: Destination file path.
        adjacency: Distance matrix indexed by new rack ids.
    """
    n = len(adjacency)
    lines = [str(n)]
    for i in range(n):
        lines.append(" ".join(str(int(x)) for x in adjacency[i]))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_aisle_racks(out_path: Path, aisles_racks: List[List[int]]) -> None:
    """
    Write aisle_racks.txt for the sub-instance.

    Args:
        out_path: Destination file path.
        aisles_racks: Aisle structure as lists of new rack ids.
    """
    lines = [str(len(aisles_racks))]
    for racks_a in aisles_racks:
        lines.append(" ".join([str(len(racks_a))] + [str(int(r)) for r in racks_a]))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_product_circuit(out_path: Path, product_circuit: List[int]) -> None:
    """
    Write product_circuit.txt for the sub-instance.

    Args:
        out_path: Destination file path.
        product_circuit: Mapping new product id -> new circuit id.
    """
    lines = [str(len(product_circuit))] + [str(int(c)) for c in product_circuit]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_orders(out_path: Path, orders: List[List[int]]) -> None:
    """
    Write orders.txt for the sub-instance.

    Args:
        out_path: Destination file path.
        orders: Orders as lists of new product ids.
    """
    lines = [str(len(orders))]
    for order in orders:
        lines.append(" ".join([str(len(order))] + [str(int(p)) for p in order]))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    """
    CLI entrypoint to generate a reduced instance for MILP benchmarking.

    The command:
    - selects products (top or stratified by circuit),
    - filters and limits orders,
    - selects racks using a compact aisle-preserving policy,
    - remaps products/racks/circuits to dense ids,
    - writes a complete instance directory with updated metadata.
    """
    ap = argparse.ArgumentParser(description="Generate a MILP-friendly sub-instance (products + orders + racks).")
    ap.add_argument("--instance-dir", required=True, help="Chemin vers le dossier instance source")
    ap.add_argument("--out-dir", required=True, help="Chemin vers le dossier instance cible à créer")

    ap.add_argument("--n-products", type=int, required=True, help="Nombre de produits P' à garder")
    ap.add_argument("--select", choices=["top", "stratified"], default="top", help="Méthode de sélection produits")

    ap.add_argument("--order-mode", choices=["strict", "intersect"], default="intersect", help="Filtrage commandes")
    ap.add_argument("--min-order-size", type=int, default=2, help="Taille min d'une commande conservée")
    ap.add_argument("--max-orders", type=int, default=500, help="Limite de commandes K' (0 = pas de limite)")
    ap.add_argument("--order-limit", choices=["first", "random", "largest"], default="largest", help="Stratégie limitation commandes")

    ap.add_argument("--max-racks", type=int, default=250, help="Nombre max de racks R' (0 = garder tous)")
    ap.add_argument("--keep-endpoints", action="store_true", help="Forcer à garder racks 0 et (R-1) si possible")
    ap.add_argument("--target-per-aisle", type=int, default=10, help="Cible racks par allée (ex: 10 => 20 racks -> 2 allées)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-circuits", type=int, default=0, help="Limiter à C' circuits (0 = pas de limite)")

    args = ap.parse_args()

    src_dir = Path(args.instance_dir)
    out_dir = Path(args.out_dir)
    rng = random.Random(args.seed)

    I = WarehouseLoader(str(src_dir)).load_all()

    n_products_old = int(I.metadata["num_products"])
    if args.n_products <= 0 or args.n_products > n_products_old:
        raise ValueError(f"--n-products doit être dans [1, {n_products_old}]")

    # Select products according to the requested policy.
    pop = compute_popularity(I.orders, n_products_old)
    if args.select == "top":
        selected_old = select_top_products(pop, args.n_products)
    else:
        selected_old = select_stratified_by_circuit(I.product_circuit, pop, args.n_products)

    # Enforce exact cardinality by dedup + backfill in popularity order.
    selected_old = sorted(set(selected_old))
    if len(selected_old) != args.n_products:
        ranked = select_top_products(pop, n_products_old)
        sel = set(selected_old)
        for p in ranked:
            if p not in sel:
                selected_old.append(p)
                sel.add(p)
            if len(selected_old) == args.n_products:
                break
        selected_old = selected_old[: args.n_products]

    # Optionally restrict selection to the most popular circuits (then backfill within that set).
    if args.max_circuits and args.max_circuits > 0:
        pop = compute_popularity(I.orders, n_products_old)
        by_c: Dict[int, int] = defaultdict(int)
        for p, c in enumerate(I.product_circuit):
            by_c[int(c)] += pop[p]

        keep_circuits = [c for c, _ in sorted(by_c.items(), key=lambda kv: kv[1], reverse=True)[: args.max_circuits]]
        keep_set = set(keep_circuits)

        selected_old = [p for p in selected_old if int(I.product_circuit[p]) in keep_set]

        if len(selected_old) < args.n_products:
            ranked = select_top_products(pop, n_products_old)
            sel = set(selected_old)
            for p in ranked:
                if p in sel:
                    continue
                if int(I.product_circuit[p]) not in keep_set:
                    continue
                selected_old.append(p)
                sel.add(p)
                if len(selected_old) == args.n_products:
                    break

        selected_old = selected_old[: args.n_products]

    mapping_p = build_mapping(selected_old)

    # Remap circuits to 0..C'-1 over the selected products.
    circuits_old = [int(I.product_circuit[p_old]) for p_old in selected_old]
    uniq = sorted(set(circuits_old))
    cmap = {c_old: i for i, c_old in enumerate(uniq)}
    product_circuit_new = [cmap[c] for c in circuits_old]

    # Filter/remap orders and apply the configured order cap.
    orders_new = filter_and_remap_orders(I.orders, mapping_p, args.order_mode, args.min_order_size)
    orders_new = limit_orders(orders_new, args.max_orders, rng, args.order_limit)

    # Select and remap racks using the compact aisle-preserving policy.
    num_racks_old = int(I.metadata["num_racks"])
    racks_kept_old = pick_racks_compact(
        aisles_racks=I.aisles_racks,
        num_racks=num_racks_old,
        max_racks=args.max_racks,
        keep_endpoints=args.keep_endpoints,
        target_per_aisle=args.target_per_aisle,
    )

    rack_map, rack_capacity_new, adjacency_new, aisles_racks_new = remap_racks(
        racks_kept_old=racks_kept_old,
        rack_capacity_old=I.rack_capacity,
        adjacency_old=I.adjacency,
        aisles_racks_old=I.aisles_racks,
    )

    # Recreate output directory from scratch to keep generated instances clean.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_adjacency(out_dir / "rack_adjacency_matrix.txt", adjacency_new)
    write_aisle_racks(out_dir / "aisle_racks.txt", aisles_racks_new)
    write_rack_capacity(out_dir / "rack_capacity.txt", rack_capacity_new)
    write_product_circuit(out_dir / "product_circuit.txt", product_circuit_new)
    write_orders(out_dir / "orders.txt", orders_new)

    total_slots_new = sum(rack_capacity_new)
    write_metadata(
        out_dir / "metadata.txt",
        I.metadata,
        new_num_racks=len(rack_capacity_new),
        new_total_slots=total_slots_new,
        new_num_products=len(product_circuit_new),
        new_num_orders=len(orders_new),
        new_num_aisles=len(aisles_racks_new),
        new_num_circuits=len(uniq),
    )

    # Trace remapping files for debugging and reproducibility.
    help_dir = out_dir / "help"
    help_dir.mkdir(parents=True, exist_ok=True)
    (help_dir / "product_old_to_new.txt").write_text(
        "\n".join(["old new"] + [f"{old} {mapping_p[old]}" for old in selected_old]) + "\n",
        encoding="utf-8",
    )
    (help_dir / "rack_old_to_new.txt").write_text(
        "\n".join(["old new"] + [f"{old} {rack_map[old]}" for old in racks_kept_old]) + "\n",
        encoding="utf-8",
    )

    print("[OK] wrote MILP-friendly sub-instance:", out_dir)
    print(f"     products: {len(product_circuit_new)} (from {n_products_old})")
    print(f"     orders  : {len(orders_new)} (from {int(I.metadata['num_orders'])})")
    print(f"     racks   : {len(rack_capacity_new)} (from {num_racks_old})")
    print(f"     select  : {args.select}")
    print(
        f"     orders  : {args.order_mode} (min_size={args.min_order_size}, "
        f"limit={args.order_limit}, max_orders={args.max_orders})"
    )
    print(
        f"     racks   : max_racks={args.max_racks} keep_endpoints={args.keep_endpoints} "
        f"target_per_aisle={args.target_per_aisle}"
    )
    print(f"     total_slots_new={total_slots_new}")


if __name__ == "__main__":
    main()