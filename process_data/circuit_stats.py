from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Sequence, Tuple

from process_data.instance_api import adapt


@dataclass(frozen=True)
class Artifact:
    """
    Lightweight container for a computed artifact emitted by a data-processing step.

    Attributes:
        name: Output filename (or stable identifier) used by downstream pipeline stages.
        kind: Contract type of the artifact payload (e.g., "vector", "edges").
        data: Raw payload stored as a sequence (kept generic to support multiple artifact kinds).
    """
    name: str
    kind: str
    data: Sequence


def compute(instance, args=None) -> List[Artifact]:
    """
    Compute circuit-level statistics and inter-circuit co-occurrence edges from orders.

    Produced signals:
    - circuit_popularity: number of orders containing at least one product of the circuit (binary per order),
    - circuit_turnover: total number of product picks attributed to the circuit (with multiplicity),
    - product_share_in_circuit: per-product turnover share inside its circuit,
    - circuit_pair_cooc: co-occurrence counts between pairs of circuits across orders.

    Args:
        instance: Raw instance object consumed by the processing pipeline.
        args: Optional namespace for runtime parameters (unused here, kept for API consistency).

    Returns:
        A list of Artifact objects describing the produced outputs.
    """
    # Adapt the instance into a stable API (products, circuits, orders, and product->circuit mapping).
    api = adapt(instance)
    n, C = api.n_products, api.n_circuits
    orders = api.orders
    c_of_p = api.product_circuit

    circuit_pop = [0] * C
    circuit_turn = [0] * C
    product_turn = [0] * n

    pair_cooc: Dict[Tuple[int, int], int] = {}

    for order in orders:
        # Accumulate turnover per product and propagate it to the product's circuit (with multiplicity).
        for p in order:
            if 0 <= p < n:
                product_turn[p] += 1
                circuit_turn[c_of_p[p]] += 1

        # Accumulate circuit popularity (binary presence per order).
        circuits_in_order = sorted({c_of_p[p] for p in set(order) if 0 <= p < n})
        for c in circuits_in_order:
            circuit_pop[c] += 1

        # Accumulate co-occurrence counts for each circuit pair appearing together in the order.
        if len(circuits_in_order) > 1:
            for a, b in combinations(circuits_in_order, 2):
                pair_cooc[(a, b)] = pair_cooc.get((a, b), 0) + 1

    # Compute per-product share of turnover inside its circuit (normalized by circuit total turnover).
    total_turn_by_c = [0] * C
    for p in range(n):
        total_turn_by_c[c_of_p[p]] += product_turn[p]

    share = [
        (product_turn[p] / total_turn_by_c[c_of_p[p]]) if total_turn_by_c[c_of_p[p]] else 0.0
        for p in range(n)
    ]

    # Build edge list (a, b, weight) and sort by decreasing weight for deterministic output.
    edges = [(a, b, float(v)) for (a, b), v in pair_cooc.items()]
    edges.sort(key=lambda x: (-x[2], x[0], x[1]))

    return [
        Artifact("circuit_popularity.txt", "vector", circuit_pop),
        Artifact("circuit_turnover.txt", "vector", circuit_turn),
        Artifact("product_share_in_circuit.txt", "vector", share),
        Artifact("circuit_pair_cooc.txt", "edges", edges),
    ]