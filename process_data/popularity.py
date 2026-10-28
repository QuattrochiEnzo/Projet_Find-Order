from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from process_data.instance_api import adapt


@dataclass(frozen=True)
class Artifact:
    """Container for a single output artifact produced by a processing step.

    Attributes:
        name: Target filename to be written by the pipeline runner.
        kind: Logical artifact type used by downstream tooling (e.g., "vector", "edges").
        data: Payload associated with the artifact (typically a list/sequence).
    """

    name: str
    kind: str  # "vector" | "edges"
    data: Sequence


def _competition_ranks(scores: List[int]) -> List[int]:
    """Compute competition ranks (1, 1, 3, 4, ...) for an integer score vector.

    This produces "competition ranking" (also known as "1224 ranking"):
    equal scores share the same rank, and the next distinct score receives
    the rank it would have in the sorted list.

    Args:
        scores: Per-item integer scores.

    Returns:
        A length-n integer vector ranks where ranks[i] is the competition rank
        of item i (1 = best / highest score).
    """
    pairs = sorted(((s, pid) for pid, s in enumerate(scores)), key=lambda x: (-x[0], x[1]))
    ranks = [0] * len(scores)
    prev_score, current_rank = None, 0
    for idx, (score, pid) in enumerate(pairs, start=1):
        # Assign the same rank to ties; advance rank only when the score changes.
        if prev_score is None or score != prev_score:
            current_rank = idx
            prev_score = score
        ranks[pid] = current_rank
    return ranks


def compute(instance) -> List[Artifact]:
    """Compute popularity and turnover scores (and ranks) from order data.

    Definitions:
        - Turnover: total number of occurrences of a product across all orders
          (counts duplicates within an order).
        - Popularity: number of orders in which the product appears at least once
          (duplicates within an order are ignored).

    Both metrics are also converted into competition ranks for easier comparison.

    Args:
        instance: Raw instance object accepted by process_data.instance_api.adapt.

    Returns:
        Four artifacts:
            - "popularity_score.txt": per-product popularity (order frequency)
            - "popularity_rank.txt": competition ranks of popularity
            - "turnover_score.txt": per-product turnover (raw occurrences)
            - "turnover_rank.txt": competition ranks of turnover
    """
    api = adapt(instance)
    n_products, orders = api.n_products, api.orders

    # Accumulate per-product order frequency and raw occurrence counts.
    popularity = [0] * n_products
    turnover = [0] * n_products

    for order in orders:
        # Track unique products in the order for popularity, while counting occurrences for turnover.
        seen = set()
        for p in order:
            if 0 <= p < n_products:
                turnover[p] += 1
                seen.add(p)
        for p in seen:
            popularity[p] += 1

    return [
        Artifact("popularity_score.txt", "vector", popularity),
        Artifact("popularity_rank.txt", "vector", _competition_ranks(popularity)),
        Artifact("turnover_score.txt", "vector", turnover),
        Artifact("turnover_rank.txt", "vector", _competition_ranks(turnover)),
    ]