# RL/operators/swap_products_operator.py
"""
Intra-circuit product swap operator.

This operator:
- selects one circuit (typically weighted by size via env.pick_destroy_circuit())
- samples random pairs of products within that circuit
- proposes swaps of their assigned racks

Rationale:
- swapping products within the same circuit preserves circuit membership
- swaps are simple, cheap to evaluate, and can fine-tune placements locally
- since products are unit-sized, rack capacities remain unchanged after a swap
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from .base_operator import Candidate


class SwapProductsOperator:
    """Random product-pair swaps restricted to a single circuit."""
    name = "swap_products_intra_circuit"

    def __init__(self, topk: int):
        self.topk = int(topk)                      # Number of candidates to generate
        self._cur_circuit: int | None = None       # Circuit selected for current step

    def prepare(self, env: Any) -> List[Candidate]:
        """
        Select a circuit and generate product swap candidates.

        Strategy:
        - Pick one circuit using environment heuristic.
        - List all products belonging to that circuit.
        - If fewer than 2 products exist, return a padded degenerate candidate list.
        - Otherwise sample random distinct pairs until topk candidates are produced.
        """
        c = int(env.pick_destroy_circuit())                               # Select circuit (often size-weighted)
        self._cur_circuit = c                                             # Store circuit id for traceability

        prods = np.where(env.product_circuit == c)[0].astype(np.int64)    # Products in chosen circuit
        if prods.size < 2:
            p0 = int(prods[0]) if prods.size else 0                       # Safe fallback product id
            out = [Candidate("swap_products", {"p1": p0, "p2": p0})]      # Degenerate self-swap
            while len(out) < self.topk:
                out.append(out[-1])                                       # Pad to fixed size
            return out

        rng = env.rng                                                     # Environment RNG (reproducible)
        cands: List[Candidate] = []                                       # Candidate swap list
        tries = 0                                                         # Attempt counter (avoid infinite loops)
        while len(cands) < self.topk and tries < self.topk * 8:
            p1 = int(rng.choice(prods))                                   # Sample first product
            p2 = int(rng.choice(prods))                                   # Sample second product
            if p1 == p2:
                tries += 1                                                # Reject identical pair
                continue
            cands.append(Candidate("swap_products", {"p1": p1, "p2": p2}))  # Append swap candidate
            tries += 1

        if not cands:
            cands = [Candidate("swap_products", {"p1": int(prods[0]), "p2": int(prods[1])})]  # Fallback pair

        while len(cands) < self.topk:
            cands.append(cands[-1])                                       # Pad to fixed action space size

        return cands

    def apply(self, env: Any, candidate: Candidate) -> Tuple[bool, Dict[str, Any]]:
        """
        Apply the product swap described by `candidate`.

        Guardrails:
        - reject bad candidate kind
        - reject identical products
        - reject unassigned products
        - reject swaps involving special/unusable racks
        """
        if candidate.kind != "swap_products":
            return False, {"error": "bad_candidate_kind", "kind": candidate.kind}  # Guard candidate type

        p1 = int(candidate.payload["p1"])                               # Product 1 id
        p2 = int(candidate.payload["p2"])                               # Product 2 id
        if p1 == p2:
            return False, {"error": "same_product"}                      # Reject degenerate swap

        r1 = int(env.assignment[p1])                                    # Rack of product 1
        r2 = int(env.assignment[p2])                                    # Rack of product 2
        if r1 < 0 or r2 < 0:
            return False, {"error": "unassigned_product"}               # Require both assigned
        if env.rack_capacities[r1] <= 0 or env.rack_capacities[r2] <= 0:
            return False, {"error": "special_rack"}                     # Reject special/blocked racks

        env.assignment[p1], env.assignment[p2] = r2, r1                 # Swap rack assignments (unit size)
        return True, {                                                  # Return move metadata
            "swapped_products": [p1, p2],
            "racks": [r1, r2],
            "repair_ok": True,
        }