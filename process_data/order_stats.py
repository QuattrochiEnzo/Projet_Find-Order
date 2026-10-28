from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from process_data.instance_api import adapt


@dataclass(frozen=True)
class Artifact:
    """Container for a single output artifact produced by a processing step.

    Attributes:
        name: Target filename to be written by the pipeline runner.
        kind: Logical artifact type used by downstream tooling (e.g., "vector").
        data: Payload associated with the artifact (typically a list/sequence).
    """

    name: str
    kind: str
    data: Sequence


def compute(instance, args=None) -> List[Artifact]:
    """Compute the average order size conditional on the presence of each product.

    For each product p, this computes the mean number of unique products in orders
    that contain p. Duplicates within an order are ignored, and product ids outside
    [0, n) are discarded.

    Args:
        instance: Raw instance object accepted by process_data.instance_api.adapt.
        args: Unused argparse-like namespace (kept for pipeline compatibility).

    Returns:
        A single artifact:
            - "avg_order_size_when_p.txt": length-n vector where avg[p] is the mean
              unique order size among orders containing p (0.0 if p never appears).
    """
    api = adapt(instance)
    n, orders = api.n_products, api.orders

    # Accumulate total unique order sizes for each product over containing orders.
    sum_sizes = [0] * n
    counts = [0] * n

    for order in orders:
        # Deduplicate within the order and filter invalid product ids.
        unique = {p for p in order if 0 <= p < n}
        size = len(unique)
        for p in unique:
            sum_sizes[p] += size
            counts[p] += 1

    # Convert totals into per-product conditional averages.
    avg = [(sum_sizes[p] / counts[p]) if counts[p] else 0.0 for p in range(n)]
    return [Artifact("avg_order_size_when_p.txt", "vector", avg)]