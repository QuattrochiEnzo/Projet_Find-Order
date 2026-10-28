from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from process_data.instance_api import adapt


@dataclass(frozen=True)
class Artifact:
    """Container for a single output artifact produced by a processing step.

    Attributes:
        name: Target filename to be written by the pipeline runner.
        kind: Logical artifact type used by downstream tooling (e.g., "vector", "lines").
        data: Payload associated with the artifact (typically a list/sequence).
    """

    name: str
    kind: str
    data: Sequence


def compute(instance, args=None) -> List[Artifact]:
    """Extract and count circuit-composition patterns observed in orders.

    Each order is mapped to the sorted set of circuits present among its valid products.
    The result is a frequency table over these circuit patterns, truncated to the top-N
    most frequent patterns with deterministic tie-breaking.

    Args:
        instance: Raw instance object accepted by process_data.instance_api.adapt.
        args: Optional argparse-like namespace supporting:
            - top_patterns: int (default 50); number of patterns to keep

    Returns:
        A single artifact:
            - "top_circuit_patterns.txt": kind="lines", where each line is:
              "<count> <circuit_1> <circuit_2> ...", with circuits sorted ascending.
    """
    api = adapt(instance)
    n, orders = api.n_products, api.orders
    c_of_p = api.product_circuit

    top = getattr(args, "top_patterns", 50)

    # Count per-order circuit signature occurrences.
    counts: Dict[Tuple[int, ...], int] = {}
    for order in orders:
        circuits = sorted({c_of_p[p] for p in set(order) if 0 <= p < n})
        key = tuple(circuits)
        counts[key] = counts.get(key, 0) + 1

    # Select the top patterns with deterministic ordering on ties.
    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]

    # Emit a "lines" artifact: each line starts with the count, followed by circuit ids.
    lines = [f"{cnt} " + " ".join(map(str, pat)) for pat, cnt in best]
    return [Artifact("top_circuit_patterns.txt", "lines", lines)]