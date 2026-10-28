# dqn_copy/env/observation_builder.py
"""
Observation builder for the RL environment.

This function encodes the current environment state into a fixed-size
float32 vector suitable for neural network input.

Feature design:
1) step_prog             → normalized episode progress in [0, 1]
2) destroyed_size_norm   → normalized size of last destroyed circuit
3) rem_cap_norm[a]       → normalized remaining capacity per aisle

All features are clipped to [-1, 1] for numerical stability.
"""

from __future__ import annotations

import numpy as np


def build_observation(env) -> np.ndarray:
    """
    Build a compact vector observation from environment state.

    Returned vector layout:
        [ step_progress,
          destroyed_size_norm,
          remaining_capacity_norm_per_aisle... ]
    """
    step_prog = np.float32(env.cur_step / max(1, env.max_steps))            # Normalize episode progress
    destroyed_size_norm = np.float32(                                       # Normalize destroyed circuit size
        env.last_destroyed_size / max(1, env.num_products)
    )

    rem_cap = np.zeros(env.num_aisles, dtype=np.float32)                    # Remaining capacity per aisle
    total_cap = float(np.sum(env.rack_capacities[env.rack_capacities > 0])) # Total usable capacity
    total_cap = max(1.0, total_cap)                                         # Guard division by zero

    for a in range(env.num_aisles):
        racks = np.asarray(env.aisles_racks[a], dtype=np.int64)             # Rack ids in aisle
        racks = racks[env.rack_capacities[racks] > 0]                       # Filter usable racks
        rem_cap[a] = float(np.sum(env.rack_remaining[racks]))               # Sum remaining capacity

    rem_cap_norm = rem_cap / total_cap                                      # Normalize by global capacity

    obs = np.concatenate(                                                    # Concatenate all features
        [
            np.array([step_prog, destroyed_size_norm], dtype=np.float32),
            rem_cap_norm.astype(np.float32),
        ],
        axis=0,
    )

    return np.clip(obs, -1.0, 1.0).astype(np.float32)                        # Clip for stability