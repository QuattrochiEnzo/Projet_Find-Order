# RL/operators/swap_circuits_operator.py
"""
Circuit swap operator.

This operator attempts to swap the *current rack intervals* of two circuits:
- read each circuit interval [L, R] from the current assignment
- destroy both circuits
- repair circuit c1 into c2's former interval
- repair circuit c2 into c1's former interval

Candidate generation is intentionally lightweight:
we build circuit pairs among the largest circuits first to focus on moves
with potentially higher impact.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from .base_operator import Candidate


class SwapCircuitsOperator:
    """Swap two circuits by exchanging their current placement intervals."""
    name = "swap_circuits"

    def __init__(self, topk: int):
        self.topk = int(topk)                          # Maximum number of candidate pairs
        self._pairs: list[tuple[int, int]] = []        # Cached circuit pairs for the step

    def prepare(self, env: Any) -> List[Candidate]:
        """
        Generate candidate circuit pairs for interval swapping.

        Heuristic:
        - Compute circuit sizes (#products).
        - Sort circuits by decreasing size.
        - Enumerate pairs among the top 32 largest circuits.
        - Return up to topk pairs (duplicate last if needed).
        """
        sizes = np.array(                                              # Circuit sizes
            [np.sum(env.product_circuit == c) for c in range(env.num_circuits)],
            dtype=np.int64,
        )
        order = np.argsort(-sizes)                                     # Largest circuits first

        pairs: list[tuple[int, int]] = []                              # Candidate (c1, c2) list
        top = order[: min(env.num_circuits, 32)]                       # Restrict enumeration scope
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                c1 = int(top[i])                                      # First circuit id
                c2 = int(top[j])                                      # Second circuit id
                if c1 != c2:
                    pairs.append((c1, c2))                             # Append valid pair
                if len(pairs) >= self.topk:
                    break                                              # Stop once budget reached
            if len(pairs) >= self.topk:
                break                                                  # Stop once budget reached

        if not pairs:
            pairs = [(0, 0)]                                           # Fallback dummy pair

        self._pairs = pairs[:]                                         # Cache pairs for debugging
        cands = [Candidate("swap_circuits", {"c1": c1, "c2": c2}) for (c1, c2) in self._pairs]  # Wrap payloads

        while len(cands) < self.topk:
            cands.append(cands[-1])                                    # Pad to fixed action space size

        return cands

    def apply(self, env: Any, candidate: Candidate) -> Tuple[bool, Dict[str, Any]]:
        """
        Apply the interval swap move described by `candidate`.

        Returns:
            (ok, details)
            - ok:      True if both repairs succeed
            - details: diagnostics including destroyed circuits and repair metadata
        """
        if candidate.kind != "swap_circuits":
            return False, {"error": "bad_candidate_kind", "kind": candidate.kind}  # Guard bad candidate

        c1 = int(candidate.payload["c1"])                               # Circuit 1 id
        c2 = int(candidate.payload["c2"])                               # Circuit 2 id
        if c1 == c2:
            return False, {"error": "same_circuit"}                      # Reject degenerate swap

        L1, R1 = env.get_circuit_interval(c1)                           # Interval of circuit 1
        L2, R2 = env.get_circuit_interval(c2)                           # Interval of circuit 2
        if L1 is None or L2 is None:
            return False, {"error": "empty_interval"}                   # Reject empty / missing interval

        env.destroy_circuit(c1)                                         # Remove circuit 1 products
        env.destroy_circuit(c2)                                         # Remove circuit 2 products

        ok1, det1 = env.repair_circuit_interval(                        # Place c1 into c2's former interval
            c1, Candidate("interval", {"L": int(L2), "R": int(R2)})
        )
        if not ok1:
            return False, {                                             # Early exit on first repair failure
                "destroyed": [c1, c2],
                "repair_ok": False,
                "failed": "c1_into_c2",
            }

        ok2, det2 = env.repair_circuit_interval(                        # Place c2 into c1's former interval
            c2, Candidate("interval", {"L": int(L1), "R": int(R1)})
        )
        if not ok2:
            return False, {                                             # Early exit on second repair failure
                "destroyed": [c1, c2],
                "repair_ok": False,
                "failed": "c2_into_c1",
            }

        return True, {                                                  # Both repairs succeeded
            "destroyed": [c1, c2],
            "repair": [det1, det2],
            "repair_ok": True,
        }