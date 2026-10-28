from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from process_data.instance_api import adapt
from process_data.neighbors import compute as compute_neighbors


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


def _competition_ranks(scores: List[float]) -> List[int]:
    """
    Compute competition ranks (a.k.a. "1224 ranking") for a list of scores.

    Ranking rule:
    - Higher scores rank first.
    - Ties share the same rank.
    - The next distinct score receives the rank it would have in a 1-indexed sorted list.

    Args:
        scores: Per-item scores to rank.

    Returns:
        A list of ranks aligned with the input ordering.
    """
    pairs = sorted(((s, pid) for pid, s in enumerate(scores)), key=lambda x: (-x[0], x[1]))
    ranks = [0] * len(scores)
    prev, current_rank = None, 0
    for idx, (score, pid) in enumerate(pairs, start=1):
        if prev is None or score != prev:
            current_rank = idx
            prev = score
        ranks[pid] = current_rank
    return ranks


def compute(instance, args=None) -> List[Artifact]:
    """
    Compute neighbor-based artifacts for an instance.

    This step:
    - adapts the raw instance to the internal API,
    - computes a per-product popularity signal (binary per order),
    - fetches neighbor edges (p, q, score) from the neighbors module,
    - aggregates neighbor scores into a per-product correlation-like vector,
    - emits the edge list and the aggregated score vector as artifacts.

    Args:
        instance: Raw instance object consumed by the processing pipeline.
        args: Optional namespace providing runtime parameters (e.g., "alpha").

    Returns:
        A list of Artifact objects describing the produced outputs.
    """
    api = adapt(instance)
    n, orders = api.n_products, api.orders

    alpha = getattr(args, "alpha", 0.7)

    # Accumulate per-product popularity as "number of orders containing the product".
    pop = [0] * n
    for order in orders:
        for p in {x for x in order if 0 <= x < n}:
            pop[p] += 1

    # Aggregate neighbor edges: (p, q, score) -> per-product sum of outgoing neighbor weights.
    neigh_art = compute_neighbors(instance, args=args)[0]
    neigh_sum = [0.0] * n
    for p, q, s in neigh_art.data:
        neigh_sum[p] += float(s)

    # Blend popularity and neighbor signal into a single score (kept for potential downstream use).
    score = [alpha * pop[p] + (1 - alpha) * neigh_sum[p] for p in range(n)]
    # --- per-product correlation = sum of its top-k neighbor scores (as provided by edges) ---
    corr = [0.0] * n
    for p, q, s in edges:
        corr[p] += float(s)

    return [
        Artifact("product_neighbors_topk.txt", "edges", edges),
        Artifact("product_correlation_score.txt", "vector", corr),
    ]