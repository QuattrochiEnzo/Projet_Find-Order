from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, Iterable, List, Sequence, Tuple

from process_data.instance_api import adapt


@dataclass(frozen=True)
class Artifact:
    """Container for a single output artifact produced by a processing step.

    Attributes:
        name: Target filename to be written by the pipeline runner.
        kind: Logical artifact type used by downstream tooling (e.g., "edges", "vector").
        data: Payload associated with the artifact (typically a list/sequence).
    """

    name: str
    kind: str
    data: Sequence


def _popularity(orders: List[List[int]], n: int) -> List[int]:
    """Compute per-product popularity as order frequency.

    Popularity is defined as the number of orders in which a product appears
    at least once (duplicates within an order are ignored). Product ids outside
    [0, n) are discarded.

    Args:
        orders: Orders as lists of product ids.
        n: Number of products.

    Returns:
        A length-n integer vector where pop[p] is the number of orders containing p.
    """
    pop = [0] * n
    for order in orders:
        for p in {x for x in order if 0 <= x < n}:
            pop[p] += 1
    return pop


def compute(instance, args=None) -> List[Artifact]:
    """Build top-k product neighbors and a correlation score from order co-occurrences.

    This step extracts undirected co-occurrence counts from customer orders and turns
    them into per-product neighbor lists using an affinity measure (Jaccard or Lift).
    It then keeps the top-k neighbors per product and derives a simple correlation
    score as the sum of retained neighbor scores.

    Supported args (via argparse Namespace):
        - k: int (default 10)
        - affinity: str in {"jaccard", "lift"} (default "jaccard")
        - max_order_size: int (default 200); skip large orders to avoid O(m^2) explosion
        - min_cooc: int (default 1); discard pairs with fewer co-occurrences

    Args:
        instance: Raw instance object accepted by process_data.instance_api.adapt.
        args: Optional argparse-like namespace providing parameters.

    Returns:
        A list of artifacts:
            - "product_neighbors_topk.txt": edge list (p, q, score) after top-k filtering
            - "product_correlation_score.txt": per-product score = sum of top-k scores
    """
    api = adapt(instance)
    n, orders = api.n_products, api.orders

    k = getattr(args, "k", 10)
    affinity = getattr(args, "affinity", "jaccard")
    max_order_size = getattr(args, "max_order_size", 200)
    min_cooc = getattr(args, "min_cooc", 1)

    pop = _popularity(orders, n)

    # Count undirected co-occurrences: (a, b) -> number of orders containing both.
    pair_count: Dict[Tuple[int, int], int] = {}

    for order in orders:
        # Deduplicate within the order and filter invalid product ids.
        s = sorted({p for p in order if 0 <= p < n})
        if len(s) <= 1:
            continue
        if len(s) > max_order_size:
            # Guard against quadratic blow-up on very large orders.
            continue
        for a, b in combinations(s, 2):
            pair_count[(a, b)] = pair_count.get((a, b), 0) + 1

    # Build per-product candidate neighbors with an affinity score.
    neigh: List[List[Tuple[int, float]]] = [[] for _ in range(n)]
    for (a, b), cooc in pair_count.items():
        if cooc < min_cooc:
            continue

        if affinity == "lift":
            # Compute Lift ~ P(a,b)/(P(a)P(b)) using order-count proxies.
            denom = pop[a] * pop[b]
            score = (cooc / denom) if denom else 0.0
        else:
            # Compute Jaccard = cooc / (pop[a] + pop[b] - cooc).
            denom = pop[a] + pop[b] - cooc
            score = (cooc / denom) if denom else 0.0

        if score > 0:
            neigh[a].append((b, score))
            neigh[b].append((a, score))

    # Keep top-k neighbors per product with deterministic tie-breaking.
    edges: List[Tuple[int, int, float]] = []
    for p in range(n):
        best = sorted(neigh[p], key=lambda x: (-x[1], x[0]))[:k]
        edges.extend((p, q, s) for q, s in best)

    # Aggregate per-product correlation as the sum of retained neighbor scores.
    corr = [0.0] * n
    for p, _, s in edges:
        corr[p] += float(s)

    return [
        Artifact("product_neighbors_topk.txt", "edges", edges),
        Artifact("product_correlation_score.txt", "vector", corr),
    ]