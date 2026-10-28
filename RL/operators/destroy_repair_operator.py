# RL/operators/destroy_repair_operator.py
"""
Destroy–Repair interval operator.

This operator:
1) Selects one circuit to "destroy" (remove all its products).
2) Generates interval-based placement candidates (bounded by topk).
3) Repairs the circuit by assigning it inside a feasible rack interval.

The interval generation mimics the sliding-window logic used in the environment:
we search contiguous rack intervals whose remaining capacity can cover
the total demand of the selected circuit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from .base_operator import Candidate, Operator


class DestroyRepairOperator:
    """
    Interval-based destroy/repair neighborhood.

    Internal state:
    - _cur_circuit: circuit selected during prepare()
    - _last_destroyed_size: number of products removed at current step
    """

    name = "destroy_repair_interval"

    def __init__(self, topk: int):
        self.topk = int(topk)                     # Maximum number of candidate intervals
        self._cur_circuit: int | None = None     # Circuit selected for current step
        self._last_destroyed_size: int = 0       # Size of destroyed circuit

    @property
    def last_destroyed_size(self) -> int:
        """Return number of products destroyed at current step."""
        return int(self._last_destroyed_size)

    @property
    def cur_circuit(self) -> int:
        """Return currently selected circuit (must exist after prepare())."""
        assert self._cur_circuit is not None
        return int(self._cur_circuit)

    def prepare(self, env: Any) -> List[Candidate]:
        """
        Select one circuit to destroy and generate interval candidates.

        Strategy:
        - Pick circuit using environment heuristic.
        - Compute total demand of that circuit.
        - For each aisle, enumerate contiguous rack intervals whose
          remaining capacity can satisfy the demand.
        - Rank intervals by (length + small tie-break on left rack index).
        - Return up to topk best intervals (duplicate last if needed).
        """
        # Select circuit to destroy
        c = int(env.pick_destroy_circuit())                   # Environment-level circuit choice
        self._cur_circuit = c                                 # Store selected circuit
        self._last_destroyed_size = int(np.sum(env.product_circuit == c))  # Compute circuit size

        demand = int(np.sum(env.product_circuit == c))        # Total demand to reassign
        if demand <= 0:
            return [Candidate("interval", {"L": 1, "R": 1})]  # Fallback trivial interval

        cands: List[Tuple[float, Candidate]] = []             # (score, candidate) pairs

        # Enumerate feasible intervals per aisle using sliding window
        for a in range(env.num_aisles):
            racks = np.asarray(env.aisles_racks[a], dtype=np.int64)  # Rack ids in aisle
            racks = np.sort(racks)                                   # Ensure increasing order
            racks = racks[env.rack_capacities[racks] > 0]             # Keep usable racks
            if racks.size == 0:
                continue                                              # Skip empty aisle

            rem = env.rack_remaining[racks].astype(np.int64)          # Remaining capacities

            L = 0                                                     # Left pointer
            cur_sum = 0                                               # Sliding window capacity sum
            for R in range(racks.size):
                cur_sum += int(rem[R])                                # Extend window right
                while L <= R and cur_sum - int(rem[L]) >= demand:
                    cur_sum -= int(rem[L])                            # Shrink from left if possible
                    L += 1
                if cur_sum >= demand:
                    Lrack = int(racks[L])                             # Left rack id
                    Rrack = int(racks[R])                             # Right rack id
                    length = (Rrack - Lrack + 1)                      # Interval length
                    score = float(length) + 1e-6 * float(Lrack)       # Rank shorter intervals first
                    cands.append((score, Candidate("interval", {"L": Lrack, "R": Rrack})))

        if not cands:
            # Fallback: choose any valid rack and duplicate until topk
            valid = np.where(env.rack_capacities > 0)[0]              # Any usable rack
            r0 = int(valid[0]) if valid.size else 1                   # Safe default rack id
            out = [Candidate("interval", {"L": r0, "R": r0})]        # Single-point interval
            while len(out) < self.topk:
                out.append(out[-1])                                   # Pad with duplicates
            return out

        cands.sort(key=lambda x: x[0])                                 # Sort by score (ascending)
        out = [cand for _, cand in cands[: self.topk]]                 # Keep topk best intervals
        while len(out) < self.topk:
            out.append(out[-1])                                         # Pad to fixed size
        return out

    def apply(self, env: Any, candidate: Candidate) -> Tuple[bool, Dict[str, Any]]:
        """
        Apply destroy–repair move:

        1) Destroy selected circuit.
        2) Attempt repair inside chosen interval.
        3) Return feasibility flag and move metadata.
        """
        c = self.cur_circuit                                          # Circuit selected in prepare()

        env.destroy_circuit(c)                                        # Remove all products of circuit

        ok, det = env.repair_circuit_interval(c, candidate)           # Attempt interval-based repair
        return bool(ok), {                                             # Return move diagnostics
            "destroyed": [c],
            "repair": [det],
            "repair_ok": bool(ok),
        }