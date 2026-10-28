"""
Solution checker for the slotting problem.

Checks performed:
1. Every product is assigned to a valid rack index
2. Rack capacities are respected (assigned products <= capacity)
3. Minimum aeration is respected per aisle
4. Circuit contiguity on the usable-rack axis (non-overlapping intervals, boundary touch allowed)
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from typing import List, Optional


def read_solution(file_path: str) -> List[int]:
    """Read rack_product_assignment.txt: line 0 = number of products, then one rack index per line."""
    with open(file_path, encoding="utf-8") as f:                                                           # open solution file (UTF-8)
        lines = [l.strip() for l in f if l.strip()]                                                        # strip and drop empty lines
    n = int(lines[0])                                                                                      # declared number of products
    if len(lines) - 1 != n:                                                                                # validate payload length
        raise ValueError(f"Fichier annonce {n} produits mais contient {len(lines) - 1} lignes.")            # keep original error wording
    return [int(lines[i + 1]) for i in range(n)]                                                           # parse rack id for each product


def check_all_products_assigned(instance, rack_of_product: List[int]) -> None:
    """Check that each product has a valid rack index (0..num_racks-1)."""
    expected = int(instance.metadata["num_products"])                                                      # expected number of products
    num_racks = len(instance.rack_capacity)                                                                # number of racks in instance

    if len(rack_of_product) != expected:                                                                   # ensure full assignment vector
        raise ValueError(f"{len(rack_of_product)} produits fournis, {expected} attendus.")                 # keep original error wording

    for p, r in enumerate(rack_of_product):                                                                # scan assignments
        if not (0 <= r < num_racks):                                                                       # validate rack index bounds
            raise ValueError(f"Produit {p} : rack {r} invalide (0 a {num_racks - 1}).")                     # keep original error wording


def check_rack_capacity(instance, rack_of_product: List[int]) -> None:
    """Check that number of assigned products per rack does not exceed capacity."""
    count = defaultdict(int)                                                                               # rack -> assigned product count
    for r in rack_of_product:                                                                              # aggregate assignments
        count[r] += 1                                                                                      # increment per rack

    for r, n in count.items():                                                                             # validate each used rack
        cap = int(instance.rack_capacity[r])                                                               # rack capacity
        if n > cap:                                                                                        # capacity violation
            raise ValueError(f"Rack {r} : {n} produits pour capacite {cap}.")                               # keep original error wording


def check_aeration(instance, rack_of_product: List[int]) -> None:
    """Check minimum aeration per aisle (free capacity >= ceil(total_cap * rate))."""
    rate_raw = float(instance.metadata["aeration_rate"])                                                   # read aeration parameter
    rate = rate_raw / 100.0 if rate_raw > 1.0 else rate_raw                                                # support "percent" or "ratio"

    count = defaultdict(int)                                                                               # rack -> assigned product count
    for r in rack_of_product:                                                                              # aggregate assignments
        count[r] += 1                                                                                      # increment per rack

    for i, aisle in enumerate(instance.aisles_racks):                                                      # scan aisles
        total_cap = sum(int(instance.rack_capacity[r]) for r in aisle)                                     # total capacity in aisle
        min_aeration = math.ceil(total_cap * rate)                                                         # required free capacity
        actual = sum(int(instance.rack_capacity[r]) - count[r] for r in aisle)                             # actual free capacity
        if actual < min_aeration:                                                                          # aeration violation
            raise ValueError(f"Allee {i} : aeration {actual}, minimum {min_aeration}.")                    # keep original error wording


def check_circuit_contiguity(instance, rack_of_product: List[int]) -> None:
    """
    Circuit contiguity check on a restricted rack axis.

    We define a "usable" rack axis made of racks with:
      - capacity > 0
      - not START/END (special boundary nodes)

    Each circuit must occupy a contiguous interval on this usable axis.
    Circuit intervals must not overlap (touching at boundaries is allowed).
    Products assigned to non-usable racks are ignored for this check.
    """
    product_circuit = list(instance.product_circuit)                                                       # product -> circuit id
    n_racks = int(instance.metadata["num_racks"])                                                          # total number of racks
    START, END = 0, n_racks - 1                                                                             # boundary rack indices

    usable = [r for r in range(n_racks)                                                                     # build contiguity axis
              if r not in (START, END) and int(instance.rack_capacity[r]) > 0]                              # keep only usable racks

    pos = {r: i for i, r in enumerate(usable)}                                                             # rack -> position on usable axis

    intervals = {}                                                                                          # circuit -> [min_pos, max_pos]
    for p, r in enumerate(rack_of_product):                                                                # scan product assignments
        c = product_circuit[p]                                                                             # circuit of product p

        if r not in pos:                                                                                   # product on non-usable rack
            continue                                                                                         # ignore for contiguity axis

        pr = pos[r]                                                                                        # usable-axis position
        if c not in intervals:                                                                             # first rack for this circuit
            intervals[c] = [pr, pr]                                                                        # initialize interval
        else:
            intervals[c][0] = min(intervals[c][0], pr)                                                     # tighten left bound
            intervals[c][1] = max(intervals[c][1], pr)                                                     # tighten right bound

    circuits = sorted(intervals.keys(), key=lambda c: (intervals[c][0], intervals[c][1]))                  # sort by interval start
    for c1, c2 in zip(circuits, circuits[1:]):                                                             # compare consecutive intervals
        if intervals[c1][1] > intervals[c2][0]:                                                            # strict overlap (no boundary touch)
            raise ValueError(
                f"Contiguite violee (usable-axis): circuit {c1} {intervals[c1]} et circuit {c2} {intervals[c2]}."
            )                                                                                               # keep original error wording


def calculate_cost(instance, rack_of_product: List[int]) -> int:
    """Compute total picking cost as sum of travel distances over all orders."""
    adj = instance.adjacency                                                                               # adjacency matrix
    start, end = 0, len(adj) - 1                                                                           # boundary nodes
    total = 0                                                                                               # accumulated cost

    for order in instance.orders:                                                                          # iterate orders
        racks = sorted({rack_of_product[p] for p in order})                                                 # unique visited racks, sorted
        path = [start] + racks + [end]                                                                     # order path with boundaries
        total += sum(adj[path[i]][path[i + 1]] for i in range(len(path) - 1))                               # accumulate arc costs

    return total                                                                                            # return total picking cost


def checker(instance, solution_dir: str, filename: str = "rack_product_assignment.txt") -> Optional[int]:
    """Run all checks, print status, and return cost if valid (None otherwise)."""
    try:
        path = os.path.join(solution_dir, filename)                                                         # build solution path
        rack_of_product = read_solution(path)                                                               # load assignment vector

        check_all_products_assigned(instance, rack_of_product)                                              # check domain of assignments
        check_rack_capacity(instance, rack_of_product)                                                      # check capacity constraints
        check_aeration(instance, rack_of_product)                                                           # check aeration constraints
        check_circuit_contiguity(instance, rack_of_product)                                                 # check circuit contiguity

        cost = calculate_cost(instance, rack_of_product)                                                    # compute objective value
        print(f"Valid solution. Cost = {cost}")                                                            # keep original success wording
        return cost                                                                                         # return computed cost

    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}")                                                                             # keep original failure wording
        return None                                                                                         # signal invalid / missing solution